"""Naming, choosing and extending the engines of a browser context.

An HTTP engine performs requests and a WebSocket engine opens sockets; a context
chooses each on its own, by name or by being handed an instance.  Which keys an
engine's ``options`` may carry is the engine's own business: each declares its
set, and a context that passes anything else is told so before it is built (see
:func:`accepted_option_keys`).

The wording is deliberate: this module may not name the context's own transport
attribute (``tests/unit/test_architecture.py``), so it talks about engines.
"""
import inspect
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .cookies import CookieJar
from .models import NetworkRequest, NetworkResponse, NetworkUnavailableError, RequestHandle

#: The two kinds of engine a context assembles, in the order users read them.
KINDS: tuple[str, str] = ("http", "ws")


@runtime_checkable
class HttpTransport(Protocol):
    """What the rest of the engine needs from an HTTP engine."""

    #: The context's cookie jar, assigned after construction so both share it.
    cookies: CookieJar

    def request_sync(self, request: NetworkRequest) -> NetworkResponse:
        """Perform *request* on the calling thread.

        ``request.redirect`` is the engine's own instruction: ``"follow"`` lets it run
        the chain by itself, and every other mode means the caller inspects the hops,
        so an engine that cannot be told to stop must declare ``FOLLOWS_ITSELF``
        rather than follow quietly.
        """

    def request_async(self, tasks, request: NetworkRequest, callback) -> RequestHandle:
        """Perform *request* off-thread, delivering ``callback(response, error)``
        on the realm's task queue."""

    def close(self) -> None:
        """Release every resource this engine owns."""


@runtime_checkable
class WebSocketTransport(Protocol):
    """What the page's WebSocket API needs from a socket engine."""

    #: The context's cookie jar, so a handshake may carry a request's cookies.
    cookies: CookieJar

    def websocket(self, tasks, url, protocols, callbacks, options=None):
        """Open a WebSocket, delivering its events on the realm's task queue."""

    def close_websocket(self, websocket) -> None:
        """Close one WebSocket this engine opened."""

    def close(self) -> None:
        """Release every resource this engine owns."""


def _typed_dict_of(annotation):
    """The TypedDict class *annotation* stands for, if it stands for one."""
    for candidate in (annotation, getattr(annotation, "__origin__", None)):
        if isinstance(candidate, type) and (
            hasattr(candidate, "__required_keys__") or hasattr(candidate, "__optional_keys__")
        ):
            return candidate
    return None


def _typed_dict_keys(annotation) -> frozenset[str] | None:
    """The keys of the TypedDict a client annotates its ``**kwargs`` with."""
    arguments = getattr(annotation, "__args__", ())
    if len(arguments) == 1 and _typed_dict_of(annotation) is None:
        # ``Unpack[SomeParams]`` is how a TypedDict reaches a ``**kwargs``.
        annotation = arguments[0]
    declared = _typed_dict_of(annotation)
    if declared is None:
        return None
    return frozenset(getattr(declared, "__annotations__", ())) or None


def signature_option_keys(target) -> frozenset[str] | None:
    """The keyword names *target* accepts, or ``None`` when it is open-ended.

    A client's own signature is the honest list of what its engine may hand it.
    One that swallows ``**kwargs`` without naming them declares nothing -- as
    does one that types its parameters to type checkers only -- so the engine
    stays unvalidated rather than being given an invented list.
    """
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return None
    names: set[str] = set()
    for name, parameter in parameters.items():
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.VAR_POSITIONAL):
            continue
        if parameter.kind is parameter.VAR_KEYWORD:
            keys = _typed_dict_keys(parameter.annotation)
            if keys is None:
                return None
            names |= keys
            continue
        names.add(name)
    return frozenset(names)


#: Built-in engines of each kind by name, plus anything a host registers.
_FACTORIES: dict[str, dict[str, Callable[..., Any]]] = {kind: {} for kind in KINDS}

#: Lazy providers of each engine's accepted keys; absent means "not validated".
_OPTION_KEYS: dict[str, dict[str, Callable[[], frozenset[str] | None]]] = {kind: {} for kind in KINDS}

#: Engines that send traffic through a proxy; absent means an open-ended engine whose ignored proxy must be refused here.
_ACCEPTS_PROXY: dict[str, dict[str, bool]] = {kind: {} for kind in KINDS}


def _kind(value: str) -> str:
    """The engine kind a caller named, checked against the two that exist."""
    kind = str(value).strip().lower()
    if kind not in _FACTORIES:
        raise ValueError(f"unknown engine kind {kind!r}; expected one of: {', '.join(KINDS)}")
    return kind


def register_transport(name: str, factory: Callable[..., Any], *,
                       option_keys: Callable[[], frozenset[str] | None] | None = None,
                       proxy: bool = False) -> None:
    """Make *factory* available as an HTTP engine name.

    The factory is called as ``factory(options=<dict or None>)`` per context, so
    one engine can serve several with different settings; registering an existing
    name replaces it.  *option_keys* returns the keys ``options`` may carry, or
    ``None`` when the engine cannot say -- which leaves it unvalidated.  It runs
    lazily and may import the client it introspects.

    *proxy* declares that this engine honours the ``proxy`` option; an engine
    that does not declare it is refused one rather than being handed a setting
    it would ignore.
    """
    _register("http", name, factory, option_keys, proxy)


def register_ws_transport(name: str, factory: Callable[..., Any], *,
                          option_keys: Callable[[], frozenset[str] | None] | None = None,
                          proxy: bool = False) -> None:
    """Make *factory* available as a WebSocket engine name; see :func:`register_transport`."""
    _register("ws", name, factory, option_keys, proxy)


def _register(kind: str, name: str, factory: Callable[..., Any],
              option_keys: Callable[[], frozenset[str] | None] | None,
              proxy: bool = False) -> None:
    key = str(name).strip()
    if not key:
        raise ValueError("a transport name must not be empty")
    if not callable(factory):
        raise TypeError("a transport factory must be callable")
    if option_keys is not None and not callable(option_keys):
        raise TypeError("a transport option-keys provider must be callable")
    _FACTORIES[kind][key] = factory
    if option_keys is None:
        _OPTION_KEYS[kind].pop(key, None)
    else:
        _OPTION_KEYS[kind][key] = option_keys
    _ACCEPTS_PROXY[kind][key] = bool(proxy)


def transport_names(kind: str = "http") -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES[_kind(kind)]))


def accepted_option_keys(name: str, kind: str = "http") -> frozenset[str] | None:
    """The option keys the *kind* engine registered as *name* accepts.

    ``None`` means it declares no set -- unregistered, or unable to enumerate
    what it takes -- and is therefore not validated.  The provider may import the
    optional client, so a missing dependency surfaces here exactly as it would
    from the engine's own constructor.
    """
    provider = _OPTION_KEYS[_kind(kind)].get(str(name).strip())
    return None if provider is None else provider()


def accepts_proxy(name: str, kind: str = "http") -> bool:
    """Whether the *kind* engine registered as *name* can be sent through a proxy.

    An engine that never declared it is taken to mean no: a proxy it cannot
    honour is a configuration mistake the caller is told about, not a setting
    quietly dropped on the way in.
    """
    return _ACCEPTS_PROXY[_kind(kind)].get(str(name).strip(), False)


def create_transport(name: str, *, kind: str = "http", options: dict | None = None,
                     strict: bool = False):
    """Build the *kind* engine registered as *name*.

    Unknown names and missing optional dependencies both raise
    :class:`NetworkUnavailableError` naming what is registered, so a typo reads
    as a configuration mistake rather than a network failure.  With *strict*, an
    option the engine does not accept raises ``TypeError`` before it is built.
    """
    which = _kind(kind)
    key = str(name).strip()
    factory = _FACTORIES[which].get(key)
    if factory is None:
        known = ", ".join(transport_names(which)) or "none"
        raise NetworkUnavailableError(
            f"unknown {which} engine {key!r}; registered engines: {known}"
        )
    if strict and options:
        accepted = accepted_option_keys(key, which)
        if accepted is not None:
            rejected = sorted(set(options) - accepted)
            if rejected:
                names = ", ".join(repr(item) for item in rejected)
                noun = "option" if len(rejected) == 1 else "options"
                listed = ", ".join(sorted(accepted)) or "none"
                raise TypeError(
                    f"unknown {noun} {names} for {which} engine {key!r}; accepted: {listed}"
                )
    return factory(options=options)


def _cronet(*, options=None):
    from .cronet import CronetTransport

    return CronetTransport(options=options)


def _curl_cffi(*, options=None):
    from .curl_cffi import CurlCffiTransport

    return CurlCffiTransport(options=options)


def _websockets(*, options=None):
    from .websockets import WebSocketsTransport

    return WebSocketsTransport(options=options)


def _cronet_keys(kind):
    from .cronet import CronetTransport

    return CronetTransport.accepted_option_keys(kind)


def _cronet_http_keys(): return _cronet_keys("http")


def _cronet_ws_keys(): return _cronet_keys("ws")


def _curl_cffi_keys():
    from .curl_cffi import CurlCffiTransport

    return CurlCffiTransport.accepted_option_keys()


def _websockets_keys():
    from .websockets import WebSocketsTransport

    return WebSocketsTransport.accepted_option_keys()


register_transport("curl_cffi", _curl_cffi, option_keys=_curl_cffi_keys, proxy=True)
# cycronet names its proxy option ``proxies``; the bridge renames the context's.
register_transport("cronet", _cronet, option_keys=_cronet_http_keys, proxy=True)
register_ws_transport("websockets", _websockets, option_keys=_websockets_keys, proxy=True)
register_ws_transport("cronet", _cronet, option_keys=_cronet_ws_keys, proxy=True)


__all__ = [
    "KINDS",
    "HttpTransport",
    "WebSocketTransport",
    "accepted_option_keys",
    "accepts_proxy",
    "create_transport",
    "register_transport",
    "register_ws_transport",
    "signature_option_keys",
    "transport_names",
]
