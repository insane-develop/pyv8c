"""Thread-safe HTTP transport bridge for the optional curl_cffi package.

It exists for curl_cffi's impersonation layer; the context's jar is the only cookie
store (the client's own is discarded, or cookies would be sent twice) and the bridge
adds no headers, so the wire carries exactly the caller's merge.
"""
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from ..foundation.logging import logger

from ..foundation.host_errors import HostStateError, LoadFailure
from .backend import RequestDelivery
from .cookies import CookieJar
from .models import (
    NetworkRequest,
    NetworkResponse,
    NetworkUnavailableError,
    cookie_header,
)
from .proxy import PROXY_OPTION, curl_proxies
from .registry import signature_option_keys

#: Per-request options forwarded straight to ``curl_cffi``; others are ignored here.
_EXTRA_KWARGS = (
    "impersonate", "ja3", "akamai", "perk", "extra_fp",
    "proxies", "proxy", "proxy_auth", "verify", "cert",
    "http_version", "interface", "doh_url",
)


def _curl_cffi_module():
    """The optional ``curl_cffi.requests`` module, or an error naming what is missing."""
    try:
        return importlib.import_module("curl_cffi.requests")
    except ImportError as exc:
        raise NetworkUnavailableError(
            "http_engine='curl_cffi' requires the optional 'curl_cffi' package"
        ) from exc


def _curl_cffi_session_type(module):
    session_type = getattr(module, "Session", None)
    if session_type is None:
        raise NetworkUnavailableError("installed curl_cffi does not expose requests.Session")
    return session_type


class CurlCffiTransport(RequestDelivery):
    """HTTP transport backed by one curl_cffi session.

    ``use_thread_local_curl`` (curl_cffi's default) gives each thread its own
    handle, so one instance serves both the synchronous and the pooled paths
    without a lock.  It performs requests only: sockets come from whichever
    WebSocket engine the context assembled alongside it.
    """

    #: Options this bridge consumes itself: the pool size, ``default_headers``, and
    #: the proxy, which is applied per request rather than handed to the session.
    bridge_option_keys = frozenset({"default_headers", "max_workers", PROXY_OPTION})

    @classmethod
    def accepted_option_keys(cls) -> frozenset[str] | None:
        """The keys ``options=`` may carry: the bridge's, plus the session's own.

        curl_cffi declares its session parameters to type checkers only, so ``None`` at
        runtime says no list; the session refuses an unknown key when built.
        """
        session_keys = signature_option_keys(_curl_cffi_session_type(_curl_cffi_module()))
        if session_keys is None:
            return None
        return session_keys | cls.bridge_option_keys

    def __init__(self, *, options=None):
        self.options = dict(options or {})
        # Ours, not the session's: consume it here and never forward it.
        max_workers = int(self.options.pop("max_workers", 4))
        default_headers = bool(self.options.pop("default_headers", False))
        self.proxies = curl_proxies(self.options.pop(PROXY_OPTION, None))
        self.curl_cffi = _curl_cffi_module()
        self.session = self._new_session(default_headers)
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pyv8c-curl")
        self.cookies = CookieJar()
        self._closed = False
        self._lock = RLock()

    def _new_session(self, default_headers):
        session_type = _curl_cffi_session_type(self.curl_cffi)
        options = dict(self.options)
        options.setdefault("default_headers", default_headers)
        try:
            return session_type(**options)
        except TypeError as exc:
            # No key list to check against: name where the option came from.
            raise TypeError(
                f"http_options was refused by the curl_cffi session: {exc}"
            ) from exc

    def _call(self, request: NetworkRequest):
        if self._closed:
            raise HostStateError("network backend is closed")
        logger.debug("network download: {} {}", request.method, request.url)
        headers = dict(request.headers)
        cookie = cookie_header(self.cookies, request)
        if cookie and "cookie" not in {str(name).lower() for name in headers}:
            headers["Cookie"] = cookie
        options = request.options or {}
        allow_redirects = options.get("allow_redirects")
        kwargs = {
            "headers": headers,
            "discard_cookies": True,
            # Only "follow" wants a chain; every other mode inspects the hops itself.
            "allow_redirects": request.redirect == "follow" if allow_redirects is None else bool(allow_redirects),
        }
        if request.body is not None:
            kwargs["data"] = request.body
        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        if self.proxies is not None:
            # Set before per-request options so the caller keeps the last word, and always set: libcurl would else read HTTP_PROXY itself.
            kwargs["proxies"] = dict(self.proxies)
        for name in _EXTRA_KWARGS:
            if options.get(name) is not None:
                kwargs[name] = options[name]
        started = time.perf_counter()
        try:
            raw = self.session.request(str(request.method).upper(), request.url, **kwargs)
        except Exception as exc:  # every transport failure reads the same here
            raise LoadFailure(f"{request.method} {request.url} failed: {exc}") from exc
        response = self._response(raw, request.url, time.perf_counter() - started)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self.cookies.add_from_response(response.url, set_cookie)
        return response

    @staticmethod
    def _headers(raw):
        """Response headers with lower-case names and every Set-Cookie kept.

        ``multi_items`` is the only view that sees repeated headers, and values are
        joined with ``, `` as the jar's splitter expects.
        """
        headers = getattr(raw, "headers", None)
        if headers is None:
            return {}
        collected: dict[str, list[str]] = {}
        multi = getattr(headers, "multi_items", None)
        if callable(multi):
            try:
                items = list(multi())
            except Exception:  # fall back to the plain view
                items = []
        else:
            items = []
        if not items:
            source = headers.items() if hasattr(headers, "items") else headers
            items = [(key, value) for key, value in source]
        for key, value in items:
            collected.setdefault(str(key).lower(), []).append(str(value))
        return {key: ", ".join(values) for key, values in collected.items()}

    @classmethod
    def _response(cls, raw, url, fallback_elapsed):
        elapsed = getattr(raw, "elapsed", None)
        total_seconds = getattr(elapsed, "total_seconds", None)
        body = getattr(raw, "content", None)
        return NetworkResponse(
            str(getattr(raw, "url", None) or url),
            int(getattr(raw, "status_code", 200)),
            str(getattr(raw, "reason", "") or ""),
            cls._headers(raw),
            getattr(raw, "text", "") if body is None else body,
            bool(getattr(raw, "history", ())),
            "basic",
            float(total_seconds()) if callable(total_seconds) else float(fallback_elapsed),
        )

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        close = getattr(self.session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # teardown must not raise
                logger.debug("failed to close the curl_cffi session", exc_info=True)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.cookies.clear()


__all__ = ["CurlCffiTransport"]
