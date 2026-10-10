"""Fetch request model."""
import re
from urllib.parse import urljoin, urlsplit


from ...idl import interface
from ...foundation.arguments import MISSING, abort_signal, byte_string, dictionary, enum_value, is_nullish, missing_error, usv_string
from ...foundation.arguments import IdlArgumentError
from ..page_errors import PageTypeError
from ..bom.abort import AbortSignal
from .headers import Headers


@interface('Request', None)
class Request:
    def __init__(self, url=MISSING, options=None, *_extra, services=None, _realm=None, **_):
        if url is MISSING:
            raise missing_error('Request', 'constructor', 1, 0, constructor=True)
        original = url if isinstance(url, Request) else None
        if original is None:
            url = usv_string(url)
        options = dictionary(options, name="RequestInit", prefix="Failed to construct 'Request': ")
        self._services = services or _realm
        base = self._services.realm_services.url if self._services else "about:blank"
        self._url = original._url if original else urljoin(base, url)
        self._body = options.get("body", original._body if original else None)
        self._credentials = enum_value(options.get("credentials", original._credentials if original else "same-origin"), ("omit", "same-origin", "include"))
        self._headers = Headers(options.get("headers", original._headers if original else None))
        self._method = byte_string(options.get("method", original._method if original else "GET"))
        self._mode = enum_value(options.get("mode", original._mode if original else "cors"), ("navigate", "same-origin", "no-cors", "cors"))
        self._redirect = enum_value(options.get("redirect", original._redirect if original else "follow"), ("follow", "error", "manual"))
        self._timeout = options.get("timeout")
        self._signal = self._read_signal(options, original)
        try:
            parts = urlsplit(self._url)
            valid_url = bool(parts.scheme) and not parts.username and not parts.password
            # Reading the port is what rejects a URL whose port cannot be one.
            _ = parts.port
        except ValueError:
            valid_url = False
        if not valid_url:
            raise PageTypeError("Failed to construct 'Request': Failed to parse URL")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", self._method):
            raise PageTypeError("Failed to construct 'Request': Invalid HTTP method")
        upper = self._method.upper()
        if upper in ("CONNECT", "TRACE", "TRACK"):
            raise PageTypeError("Failed to construct 'Request': Unsupported HTTP method")
        if upper in ("DELETE", "GET", "HEAD", "OPTIONS", "POST", "PUT"):
            self._method = upper
        if self._mode == "navigate":
            raise PageTypeError("Failed to construct 'Request': Cannot construct a Request with a RequestInit whose mode member is set as 'navigate'.")
        if self._mode == "no-cors" and upper not in ("GET", "HEAD", "POST"):
            raise PageTypeError("Failed to construct 'Request': Unsupported method in no-cors mode")
        if upper in ("GET", "HEAD") and not is_nullish(self._body):
            raise PageTypeError("Failed to construct 'Request': Request with GET/HEAD method cannot have body.")

    def get_url(self): return self._url
    def _read_signal(self, options, original):
        """The signal this request cancels through.

        A request always has a signal, and it is always its own: naming one in the
        init makes the request's signal follow it rather than be it, so the two are
        never the same object and the reason travels across.
        """
        try:
            given = abort_signal(options.get(
                "signal", original._signal if original else None))
        except (PageTypeError, IdlArgumentError) as exc:
            raise PageTypeError("Failed to construct 'Request': Failed to read the 'signal'"
                              " property from 'RequestInit': Failed to convert value to"
                              " 'AbortSignal'.") from exc
        signal = AbortSignal(services=self._services)
        if given is not None:
            given._subscribe(lambda: signal._abort(given._reason))
        return signal
    def get_method(self): return self._method
    def get_headers(self): return self._headers
    def get_mode(self): return self._mode
    def get_credentials(self): return self._credentials
    def get_destination(self):
        """A request a script built was not created by the browser for a target."""
        return ""
    def get_bodyUsed(self):
        # Nothing here consumes the body by streaming it; it is read in Python.
        return False
    def get_signal(self): return self._signal

__all__ = ["Request"]
