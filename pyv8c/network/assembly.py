"""Assembling one context's engines from the options the Browser was built with."""
from __future__ import annotations

from .backend import NetworkBackend
from .proxy import PROXY_OPTION, RAW_OPTION_KEYS
from .registry import accepts_proxy, create_transport

#: The method that makes an object an engine for each role.
_SERVES = {"http": "request_sync", "ws": "websocket"}


def _options(browser, role):
    """The *role* engine's options, as a copy; ``None`` when none were set."""
    return dict(getattr(browser, f"{role}_options")) or None


def _configured_raw_proxy(options):
    """The engine's own proxy keys already in *options*, if any.

    Those are how a proxy was configured before ``proxy=`` existed; the two ways of
    saying it must not be mixed, or whichever lost would be invisible.
    """
    return sorted(key for key in RAW_OPTION_KEYS if key in (options or {}))


def _with_proxy(role, name, options, proxy):
    """The *role* engine's options with the context's proxy merged in.

    The engine is asked first and one that carries no proxy traffic is handed nothing.
    For one that does, the key is always set -- ``""`` pins a direct connection, since
    leaving it out would let both engines read a proxy from the environment.
    """
    configured = _configured_raw_proxy(options)
    if configured and proxy is not None:
        listed = ", ".join(repr(key) for key in configured)
        raise TypeError(
            f"proxy= and {role}_options[{listed}] were both given for {role} engine "
            f"{name!r}; keep one -- the other would be overwritten"
        )
    if not accepts_proxy(name, role):
        if proxy is not None:
            raise TypeError(
                f"{role} engine {name!r} cannot be sent through a proxy; configure it "
                f"with {role}_options instead, or name an engine that declares one"
            )
        return options
    if configured:
        # The engine's own proxy keys are its business; it knows how to honor them.
        return options
    return {**(options or {}), PROXY_OPTION: "" if proxy is None else proxy}


def _serves(candidate, role):
    """Whether *candidate* can perform this role."""
    return hasattr(candidate, _SERVES[role])


def _engine(browser, role, value, proxy):
    """One role's engine: a registered name, a ready object, or a factory.

    A name is built from the registry with that role's options; a factory is called per
    context.  A ready engine can take no options and is refused rather than ignored.
    """
    if value is None:
        return None
    if isinstance(value, str):
        options = _with_proxy(role, value, _options(browser, role), proxy)
        return create_transport(value, kind=role, options=options or None, strict=True)
    if proxy is not None:
        raise TypeError(
            f"proxy cannot configure a ready {role}_engine; name a registered engine "
            "so the setting has somewhere to go"
        )
    engine = value() if callable(value) and not _serves(value, role) else value
    if not _serves(engine, role):
        raise TypeError(
            f"{role}_engine must be a registered engine name, an object with "
            f"{_SERVES[role]}, or a factory for one"
        )
    if getattr(browser, f"{role}_options"):
        raise TypeError(
            f"{role}_options cannot configure a ready {role}_engine; name a "
            "registered engine to pass options to it"
        )
    return engine


def _refuse_unconsumed_options(browser):
    """Reject options no engine would read, instead of dropping them quietly."""
    given = [role for role in ("http", "ws") if getattr(browser, f"{role}_options")]
    if given:
        listed = " and ".join(f"{role}_options" for role in given)
        raise TypeError(
            f"{listed} was given but no network engine is configured; "
            "set real_network=True, http_engine or ws_engine"
        )


def _refuse_ready_engines(browser):
    """A name is only a preference; a ready engine would be dropped, so it is refused."""
    for role in ("http", "ws"):
        value = getattr(browser, f"{role}_engine")
        if value is not None and not isinstance(value, str):
            raise TypeError(
                f"real_network=False assembles no engines, so {role}_engine has "
                "nothing to serve; set real_network=True to use it"
            )


def _refuse_shared_options(browser, value):
    """One engine cannot be configured twice; say which options to move."""
    if browser.ws_options:
        raise TypeError(
            f"http_engine and ws_engine both give {value!r}, which is built once, "
            "so ws_options would be ignored; give the WebSocket role its own engine "
            "or move those settings into http_options"
        )


def _refuse_proxy_without_engine(proxy):
    """A configured proxy with nothing to carry it is a mistake, not a no-op."""
    if proxy is not None:
        raise TypeError(
            "proxy was given but no network engine is configured; "
            "set real_network=True, http_engine or ws_engine"
        )


def assemble_backend(browser, proxy=None):
    """Assemble one context's engines from what the Browser was built with.

    *proxy* is the context's resolved proxy, merged into each role's options once that
    engine has accepted it; options are checked against the engine that reads them.
    """
    if not browser.real_network:
        _refuse_ready_engines(browser)
        _refuse_proxy_without_engine(proxy)
        _refuse_unconsumed_options(browser)
        return None

    http = _engine(browser, "http", browser.http_engine, proxy)
    ws_value = browser.ws_engine
    if ws_value is None:
        ws = None
    elif ws_value is browser.http_engine or (
        isinstance(ws_value, str) and ws_value == browser.http_engine
    ):
        # One name, one engine: a second build means a second client and pool.
        _refuse_shared_options(browser, ws_value)
        ws = http
    else:
        ws = _engine(browser, "ws", ws_value, proxy)

    if http is None and ws is None:
        _refuse_proxy_without_engine(proxy)
        _refuse_unconsumed_options(browser)
        return None
    return NetworkBackend(http, ws)
