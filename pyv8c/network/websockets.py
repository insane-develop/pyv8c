"""The ``websockets`` engine: the page's sockets on a real WebSocket client.

It is the default engine, so ``new WebSocket`` works with nothing optional installed.
Each socket gets its own thread running ``websockets.sync``, because a blocking
connection matches the project's thread model.
"""
import importlib
import inspect
import threading
from threading import RLock

from ..foundation.logging import logger

from ..foundation.urls import header_set as _header_set
from .models import NetworkUnavailableError
from .proxy import PROXY_OPTION, websockets_proxy

#: Options the engine consumes itself: handshake ingredients and the proxy, translated before passing on.
_BRIDGE_KEYS = frozenset({"cookies", "headers", PROXY_OPTION})

#: Keyword names the engine supplies itself; the target and handshake are its own.
_MANAGED_KEYS = frozenset({"uri", "subprotocols", "additional_headers", "logger", "origin"})


def _connect_client():
    """``websockets.sync``'s ``connect``, or an error naming what is missing."""
    try:
        module = importlib.import_module("websockets.sync.client")
    except ImportError as exc:
        raise NetworkUnavailableError(
            "the websockets network engine requires the 'websockets' package"
        ) from exc
    return module.connect


def _close_details(exc):
    """The close a raised connection reported, or ``None`` if it reported none."""
    received = getattr(exc, "rcvd", None)
    if received is not None:
        code = int(received.code)
        return code, str(received.reason), code in (1000, 1001)
    sent = getattr(exc, "sent", None)
    if sent is not None:
        return int(sent.code), str(sent.reason), True
    return None


def _recorded_close(connection):
    """The close the protocol recorded, or ``None`` if it recorded none."""
    code = getattr(connection, "close_code", None)
    if code is None:
        return None
    return int(code), str(getattr(connection, "close_reason", "") or ""), True


def _allows_cookies(url):
    """The URL whose cookies a handshake to *url* carries."""
    for scheme, equivalent in (("wss://", "https://"), ("ws://", "http://")):
        if url.startswith(scheme):
            return equivalent + url[len(scheme):]
    return url


class _Socket:
    """One connection, as the page's WebSocket API sees it."""

    #: The negotiated subprotocol; the page reads it when the socket opens.
    protocol = ""

    def __init__(self, tasks, url, protocols, callbacks, connect_kwargs):
        self._tasks = tasks
        self._url = url
        self._protocols = protocols
        self._callbacks = dict(callbacks)
        self._connect_kwargs = connect_kwargs
        self._connection = None
        self._lock = RLock()
        self._thread = threading.Thread(target=self._run, name="pyv8c-websocket", daemon=True)

    def send(self, message): self._send(message)
    def send_bytes(self, data): self._send(data)
    def close(self, code=1000, reason=""):
        with self._lock:
            connection = self._connection
        if connection is None:
            return
        try:
            connection.close(int(code), str(reason))
        except Exception as exc:
            logger.debug("websocket close failed: {}", exc)

    def _send(self, payload):
        with self._lock:
            connection = self._connection
        if connection is None:
            logger.debug("websocket send dropped: the socket is not open")
            return
        try:
            connection.send(payload)
        except Exception as exc:
            logger.debug("websocket send failed: {}", exc)

    def start(self): self._thread.start()

    def _run(self):
        self._tasks.begin_network()
        try:
            connection = _connect_client()(
                self._url, subprotocols=self._protocols or None, **self._connect_kwargs
            )
        except Exception as exc:
            logger.debug("websocket failed to open {}: {!r}", self._url, exc)
            self._deliver("error", self, exc)
            self._deliver("close", self, 1006, "", False)
            return
        finally:
            self._tasks.complete_network()
        with self._lock:
            self._connection = connection
            self.protocol = str(getattr(connection, "subprotocol", "") or "")
        logger.debug("websocket open {} as {}", self._url, self.protocol or "no subprotocol")
        self._deliver("open", self)
        details = None
        try:
            with connection:
                for message in connection:
                    self._deliver("message", self, message, isinstance(message, str))
        except Exception as exc:  # a connection ends by raising
            details = _close_details(exc)
            if details is None:
                logger.debug("websocket failed on {}: {!r}", self._url, exc)
                self._deliver("error", self, exc)
        finally:
            with self._lock:
                self._connection = None
            self._deliver("close", self, *(details or _recorded_close(connection) or (1006, "", False)))

    def _deliver(self, name, *arguments):
        """Hand one event to the page, on the realm's own task queue."""
        handler = self._callbacks.get(name)
        if handler is None:
            return
        self._tasks.post_task(lambda: handler(*arguments))


class WebSocketsTransport:
    """Opens WebSockets with ``websockets``; one connection per thread."""

    #: The context's cookie jar; a handshake carries what a request to the URL would.
    cookies = None

    @classmethod
    def accepted_option_keys(cls) -> frozenset[str] | None:
        """The keys ``ws_options`` may carry."""
        try:
            parameters = inspect.signature(_connect_client()).parameters
        except (TypeError, ValueError):
            return None
        named = {
            name for name, parameter in parameters.items()
            if parameter.kind not in (
                parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL, parameter.POSITIONAL_ONLY,
            )
        }
        return (frozenset(named) - _MANAGED_KEYS) | _BRIDGE_KEYS

    def __init__(self, *, options=None):
        self.options = dict(options or {})
        self.cookies = None
        self._sockets = set()
        self._closed = False
        self._lock = RLock()

    def websocket(self, tasks, url, protocols, callbacks, options=None):
        """Open one socket and start delivering its events on *tasks*' queue."""
        with self._lock:
            if self._closed:
                raise NetworkUnavailableError("this WebSocket engine is closed")
            socket = _Socket(
                tasks, str(url), list(protocols or []), callbacks,
                self._connect_kwargs(str(url), options),
            )
            self._sockets.add(socket)
        socket.start()
        return socket

    def close_websocket(self, websocket):
        with self._lock:
            self._sockets.discard(websocket)
        websocket.close(1000, "page closed")

    def _connect_kwargs(self, url, given):
        """The ``connect`` arguments for one socket, handshake and proxy included."""
        call = dict(given or {})
        kwargs = {name: value for name, value in self.options.items() if name not in _BRIDGE_KEYS}
        # Always given: the client's default ``True`` reads the environment, but this context connects directly.
        kwargs["proxy"] = websockets_proxy(self.options.get(PROXY_OPTION))
        user_agent, headers = self._handshake_headers(url, call)
        if user_agent is not None:
            kwargs.setdefault("user_agent_header", user_agent)
        if headers:
            kwargs.setdefault("additional_headers", headers)
        return kwargs

    def _handshake_headers(self, url, call):
        """The headers one handshake carries: the page's, then the configured ones."""
        headers = {}
        user_agent = None
        for layer in (call.get("headers"), self.options.get("headers")):
            for name, value in dict(layer or {}).items():
                lowered = str(name).lower()
                headers = {key: item for key, item in headers.items() if str(key).lower() != lowered}
                if lowered == "user-agent":
                    user_agent = str(value)
                else:
                    headers[name] = value
        if call.get("origin"):
            _header_set(headers, "Origin", str(call["origin"]))
        if self.options.get("cookies", True) and self.cookies is not None:
            cookie = self.cookies.header_for(_allows_cookies(url))
            if cookie:
                _header_set(headers, "Cookie", cookie)
        return user_agent, headers

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._sockets)
            self._sockets.clear()
        for socket in sockets:
            socket.close(1000, "context closed")


__all__ = ["WebSocketsTransport"]
