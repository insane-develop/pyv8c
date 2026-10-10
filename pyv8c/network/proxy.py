"""The proxy a context sends its traffic through, in the one shape engines read.

``None`` means nothing was configured (turned into a direct connection), ``""`` a
direct connection asked for explicitly, or an ``http``/``https`` URL whose userinfo
carries the credentials; SOCKS is refused by name.  Both engines would otherwise read
a proxy from the environment, so a context not given one is told so explicitly.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: The option key that carries a proxy into an engine, in either role.
PROXY_OPTION = "proxy"

#: Keys an engine's own options may carry a proxy in; one beside ``proxy=`` is refused.
RAW_OPTION_KEYS = ("proxy", "proxies", "proxy_auth")

#: The proxy schemes that are supported.
SCHEMES = ("http", "https")


def normalize_proxy(value):
    """Validate and canonicalize a ``proxy=`` argument.

    Returns ``None``, ``""``, or the ``http``/``https`` URL as written.  Raises
    ``TypeError`` for a non-string and ``ValueError`` when the value is not an
    http/https proxy URL, naming the reason.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            "proxy must be a URL string, \"\" to connect directly, or None to "
            "inherit, not " + type(value).__name__
        )
    text = value.strip()
    if not text:
        return ""
    if "://" not in text:
        raise ValueError(f"proxy needs a scheme: {text!r} should look like http://host:port")
    parts = urlsplit(text)
    if parts.scheme.lower() not in SCHEMES:
        raise ValueError(
            f"unsupported proxy scheme {parts.scheme!r}: only http:// and https:// "
            "proxies are supported, and SOCKS is not implemented"
        )
    if not parts.hostname:
        raise ValueError(f"proxy needs a host: {text!r}")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError(f"proxy has an unusable port: {text!r} ({exc})") from exc
    # The scheme is the one part that is case-insensitive everywhere but spelled
    # by each client their own way, so it is written the way they both read it.
    scheme_end = text.index("://")
    return text[:scheme_end].lower() + text[scheme_end:]


def curl_proxies(value):
    """The ``proxies=`` argument for one curl_cffi request.

    ``None`` means "leave the request alone": the caller configured the engine's own
    proxy keys.  Anything else becomes a mapping for both schemes.  Measured against
    curl_cffi 0.16.3: only an empty per-request value overrides ``HTTP_PROXY``/
    ``ALL_PROXY``; ``proxy=""``, ``proxies={"http": None}`` and ``proxies=""`` do not.
    """
    if value is None:
        return None
    return {"http": value, "https": value}


def websockets_proxy(value):
    """The ``proxy=`` argument for one websockets connection.

    The client's default is ``True`` ("read the environment"), so a direct connection
    is asked for with ``None``; a non-string is passed through, since ``True`` is the
    client's own way of asking for the environment's proxy.
    """
    if value is None or value == "":
        return None
    return value


__all__ = [
    "PROXY_OPTION",
    "RAW_OPTION_KEYS",
    "SCHEMES",
    "curl_proxies",
    "normalize_proxy",
    "websockets_proxy",
]
