"""Fetch response model."""
import json

import pyv8

from ...idl import interface
from ...foundation.arguments import byte_string, dictionary, integer, is_nullish, usv_string
from ...foundation.arguments import IdlArgumentError
from ..page_errors import PageRangeError, PageSyntaxError, PageTypeError
from ..page_errors import js_error_value
from .blob import Blob
from .headers import Headers


def _json_nulls(value):
    # None crosses the native bridge as undefined; JSON null must stay null,
    # including inside objects and arrays settled through a Promise resolver.
    if value is None:
        return pyv8.Null
    if isinstance(value, list):
        return [_json_nulls(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_nulls(item) for key, item in value.items()}
    return value


@interface('Response', None)
class Response:

    def __init__(self, body=None, options=None, *_extra, services=None, _realm=None, **_):
        if body is pyv8.Undefined:
            body = None
        if isinstance(body, Blob):
            body = body._data
        elif not is_nullish(body) and not isinstance(body, bytes):
            body = usv_string(body)
        if options is pyv8.Undefined:
            options = None
        if not is_nullish(options):
            options = dictionary(options)
        if services is pyv8.Undefined:
            services = None
        options = options if isinstance(options, dict) else {}
        self._services = services or _realm
        self.body = body
        self.headers = Headers(options.get("headers"))
        self.status = integer(options.get("status", 200), bits=16)
        self.status_text = byte_string(options.get("statusText", options.get("reason", "")))
        if _realm is not None and not 200 <= self.status <= 599:
            raise PageRangeError("Failed to construct 'Response': The status provided (" + str(self.status) + ") is outside the range [200, 599].")
        if _realm is not None and self.status in (204, 205, 304) and not is_nullish(body):
            raise PageTypeError("Failed to construct 'Response': Response with null body status cannot have body")
        if any(character in self.status_text for character in "\r\n"):
            raise PageTypeError("Failed to construct 'Response': Invalid statusText")
        self.url = str(options.get("url", ""))
        self.redirected = bool(options.get("redirected", False))
        # A response a script builds is "default"; "basic" is what a same-origin
        # fetch returns, and "cors"/"opaque" belong to the network layer.
        self.type = str(options.get("type", "default"))
        self.body_used = False

    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_ok(self): return 200 <= self.status < 300
    def get_url(self): return self.url
    def get_redirected(self): return self.redirected
    def get_type(self): return self.type
    def get_headers(self): return self.headers
    def get_bodyUsed(self): return self.body_used

    def fn_clone(self, *_extra):
        """A response with the same body, headers, status and type, usable on
        its own; neither side counts as consumed until something reads it.
        """
        if self.body_used:
            raise PageTypeError("Body has already been consumed.")
        return Response(self.body, {"status": self.status, "statusText": self.status_text,
                                    "headers": self.headers, "url": self.url,
                                    "redirected": self.redirected, "type": self.type},
                        services=self._services)

    def body_text(self):
        if is_nullish(self.body):
            return ""
        return self.body.decode("utf-8", "replace") if isinstance(self.body, bytes) else usv_string(self.body)

    def _body_value(self):
        if self.body_used:
            raise PageTypeError("Body has already been consumed.")
        self.body_used = True
        return self.body_text()

    def _result(self, value):
        return self._services.realm_services.promise_resolve(value) if self._services else value

    def fn_text(self, *_extra):
        if not self._services:
            return self._body_value()
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            resolve(self._body_value())
        except (PageTypeError, pyv8.JSException, UnicodeError, IdlArgumentError) as exc:
            reject(js_error_value(exc))
        return promise
    def fn_json(self, *_extra):
        if not self._services:
            return json.loads(self._body_value())
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            text = self._body_value()
            try:
                value = json.loads(text)
            except ValueError as exc:
                #: The page hears the shape a browser reports, not the parser's own
                #: wording: what went wrong where, in the terms Chrome uses.
                raise PageSyntaxError(
                    f"Unexpected token in JSON at position {getattr(exc, 'pos', 0)}"
                ) from exc
            resolve(_json_nulls(value))
        except (PageTypeError, PageSyntaxError, pyv8.JSException, UnicodeError, IdlArgumentError) as exc:
            reject(js_error_value(exc))
        return promise
    def fn_arrayBuffer(self, *_extra):
        if not self._services:
            return self._body_value().encode()
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            text = self._body_value()
            resolve(self.body if isinstance(self.body, bytes) else text.encode())
        except (PageTypeError, pyv8.JSException, UnicodeError, IdlArgumentError) as exc:
            reject(js_error_value(exc))
        return promise

__all__ = ["Response"]
