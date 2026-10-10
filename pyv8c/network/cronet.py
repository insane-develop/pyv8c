"""The cycronet engine: one optional client that can serve both roles.

Naming it for both roles shares one instance, so requests and sockets share one
connection cache.
"""
import importlib
import inspect
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from ..foundation.logging import logger  # patch target for cookie-change logging tests

from ..foundation.host_errors import HostStateError
from .backend import RequestDelivery
from .cookies import CookieJar
from .models import (
    NetworkRequest,
    NetworkResponse,
    NetworkUnavailableError,
    cookie_header,
)
from .proxy import PROXY_OPTION
from .registry import signature_option_keys


def _cycronet_module():
    """The optional cycronet package, or an error naming what is missing."""
    try:
        return importlib.import_module("cycronet")
    except ImportError as exc:
        raise NetworkUnavailableError("the 'cronet' engine requires the optional 'cycronet' package") from exc


def _cycronet_client_type(module):
    client_type = getattr(module, "CronetClient", None)
    if client_type is None:
        client_type = getattr(module, "PyCronetClient", None)
    if client_type is None:
        raise NetworkUnavailableError("installed cycronet does not expose CronetClient or PyCronetClient")
    return client_type


class CronetTransport(RequestDelivery):
    """Owns one optional cycronet client, which serves both engine roles.

    The same client performs requests and opens sockets, so naming cycronet for
    both roles hands a context one instance.  It never touches V8: what it
    delivers goes through the task capability it was given.
    """

    #: cycronet follows redirects itself and names no way to stop it, so the hop-by-hop
    #: modes are refused for it instead of being obeyed quietly (docs/network-engines.md).
    FOLLOWS_ITSELF = True

    @classmethod
    def accepted_option_keys(cls, kind: str = "http") -> frozenset[str] | None:
        """The keys the *kind* role's options may carry: the client's, plus ours.

        Leftovers go straight to the installed client, so its constructor is the list;
        the request role owns a thread pool and both roles take the context's proxy.
        """
        client_keys = signature_option_keys(_cycronet_client_type(_cycronet_module()))
        ours = {PROXY_OPTION, "max_workers"} if kind == "http" else {PROXY_OPTION}
        if client_keys is None:
            return None
        return client_keys | ours

    def __init__(self, *, options=None):
        self.options = dict(options or {})
        # Ours, not the client's: consumed here, and the proxy renamed on the way.
        max_workers = int(self.options.pop("max_workers", 4))
        proxy = self.options.pop(PROXY_OPTION, None)
        if proxy and "proxies" not in self.options:
            self.options["proxies"] = proxy
        self.cycronet = _cycronet_module()
        self.client = self._new_client()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pyv8c-cronet")
        self.cookies = CookieJar()
        self._websockets = set()
        self._closed = False
        self._lock = RLock()

    def _new_client(self): return _cycronet_client_type(self.cycronet)(**self.options)

    @staticmethod
    def _response(raw, url):
        headers = getattr(raw, "headers", {}) or {}
        if hasattr(headers, "items"):
            headers = {
                str(k).lower(): ([str(item) for item in v] if isinstance(v, (list, tuple)) else str(v))
                for k, v in headers.items()
            }
        else:
            headers = {}
        body = getattr(raw, "content", None)
        if body is None:
            body = getattr(raw, "text", "")
        if callable(body):
            body = body()
        return NetworkResponse(
            str(getattr(raw, "url", None) or url),
            int(getattr(raw, "status_code", getattr(raw, "status", 200))),
            str(getattr(raw, "reason", "")), headers, body,
            bool(getattr(raw, "history", ())),
        )

    def _call(self, request: NetworkRequest):
        if self._closed:
            raise HostStateError("network backend is closed")
        logger.debug("network download: {} {}", request.method, request.url)
        headers = dict(request.headers)
        cookie = cookie_header(self.cookies, request)
        if cookie and "cookie" not in {str(name).lower() for name in headers}:
            headers["Cookie"] = cookie
        method = request.method.lower()
        fn = getattr(self.client, method, None)
        if fn is None:
            fn = getattr(self.client, "request", None)
            if fn is None:
                raise NetworkUnavailableError(f"cycronet client has no {method} method")
            raw = fn(request.method, request.url, headers=headers, data=request.body, timeout=request.timeout)
        else:
            kwargs = {"headers": headers}
            if request.body is not None:
                kwargs["data"] = request.body
            if request.timeout is not None:
                try:
                    parameters = inspect.signature(fn).parameters.values()
                    accepts_timeout = any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "timeout"
                        for parameter in parameters
                    )
                except (TypeError, ValueError):
                    accepts_timeout = True
                if accepts_timeout:
                    kwargs["timeout"] = request.timeout
            raw = fn(request.url, **kwargs)
        response = self._response(raw, request.url)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self.cookies.add_from_response(response.url, set_cookie)
        self._absorb_client_cookies(raw, response.url)
        return response

    def _absorb_client_cookies(self, raw, url):
        jar = getattr(raw, "cookies", None)
        entries = getattr(jar, "iter_cookies", None)
        if entries is None:
            return
        try:
            cookies = list(entries())
        except Exception:
            return
        known = {
            cookie.split("=", 1)[0]
            for cookie in self.cookies.header_for(url).split("; ") if cookie
        }
        for cookie in cookies:
            name = getattr(cookie, "name", None)
            if name is None or name in known:
                continue
            path = getattr(cookie, "path", "") or "/"
            self.cookies.add_from_response(url, f"{name}={getattr(cookie, 'value', '')}; Path={path}")

    def websocket(self, page, url, protocols, callbacks, options=None):
        if self._closed:
            raise HostStateError("network backend is closed")
        factory = getattr(self.client, "websocket", None)
        if factory is None:
            raise NetworkUnavailableError("installed cycronet does not expose websocket callback API")
        callbacks = dict(callbacks)
        kwargs = {"on_open": lambda ws: page.post_task(lambda: callbacks.get("open", lambda *_: None)(ws)),
                  "on_message": lambda ws, message, is_text=True: page.post_task(lambda: callbacks.get("message", lambda *_: None)(ws, message, is_text)),
                  "on_close": lambda ws, code, reason, was_clean=True: page.post_task(lambda: callbacks.get("close", lambda *_: None)(ws, code, reason, was_clean)),
                  "on_error": lambda ws, error, net_error=None: page.post_task(lambda: callbacks.get("error", lambda *_: None)(ws, error, net_error))}
        try:
            ws = factory(url, protocols=protocols or [], **kwargs)
        except TypeError:
            ws = factory(url, **kwargs)
        with self._lock:
            self._websockets.add(ws)
        if hasattr(ws, "run_in_background"):
            ws.run_in_background()
        return ws

    def close_websocket(self, websocket):
        try:
            websocket.close(1000, "page closed")
        except Exception:
            pass
        with self._lock:
            self._websockets.discard(websocket)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._websockets)
            self._websockets.clear()
        for ws in sockets:
            try:
                ws.close(1000, "context closed")
            except Exception:  # teardown must not raise
                pass
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.cookies.clear()


__all__ = ["CronetTransport"]
