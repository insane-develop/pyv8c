"""CookieStore: the document's cookies, read and written asynchronously.

A change made here does not raise ``change`` on ``onchange`` yet, and ``expires`` is not
carried over: both are visible in the interface, neither is modelled.
"""
from __future__ import annotations

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dictionary, dom_string, is_nullish, missing_error
from ..dom.events import EventTarget

__all__ = ["CookieStore"]


@interface('CookieStore', 'EventTarget')
class CookieStore(EventTarget):
    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services or _realm, **kw)

    def get_onchange(self): return self._get_handler("change")
    def set_onchange(self, value): self._set_handler("change", value)

    def _document(self):
        return getattr(getattr(self._services, "document_services", None), "document", None)

    def _pairs(self):
        """The cookies the document sees, in the order it reports them."""
        document = self._document()
        if document is None:
            return []
        cookies = []
        for item in str(document.get_cookie()).split("; "):
            name, _, value = item.partition("=")
            if name:
                cookies.append({"name": name, "value": value})
        return cookies

    def _options(self, value):
        """The dictionary form of an argument; anything else is a name."""
        if value is None or not isinstance(value, pyv8.JSObject):
            return {"name": dom_string(value)}
        return dictionary(value, name="CookieStoreGetOptions")

    def _resolve(self, value):
        services = self._services
        if services is None:
            return pyv8.Undefined
        return services.realm_services.promise_resolve(value)

    def _write(self, text):
        document = self._document()
        if document is not None:
            document.set_cookie(text)

    def fn_get(self, name=None, *_extra):
        wanted = self._options(name).get("name") if name is not None else None
        if wanted is None:
            return self._resolve(pyv8.Null)
        for cookie in self._pairs():
            if cookie["name"] == wanted:
                return self._resolve(cookie)
        return self._resolve(pyv8.Null)

    def fn_getAll(self, options=None, *_extra):
        wanted = None if is_nullish(options) else self._options(options).get("name")
        cookies = self._pairs()
        if wanted is not None:
            cookies = [cookie for cookie in cookies if cookie["name"] == wanted]
        return self._resolve(cookies)

    def fn_set(self, name=MISSING, value=None, *_extra):
        if name is MISSING:
            raise missing_error('CookieStore', 'set', 1, 0, constructor=False)
        if isinstance(name, (str, int, float, bool)):
            cookie = {"name": dom_string(name),
                      "value": "" if value is None else dom_string(value)}
        else:
            cookie = dictionary(name, name="CookieInit")
        self._write(_cookie_string(cookie))
        return self._resolve(None)

    def fn_delete(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('CookieStore', 'delete', 1, 0, constructor=False)
        self._write(f"{self._options(name).get('name', '')}=; Max-Age=0")
        return self._resolve(None)


def _cookie_string(cookie):
    parts = [f"{cookie.get('name', '')}={cookie.get('value', '')}"]
    for key, attribute in (("domain", "domain"), ("path", "path"), ("sameSite", "samesite")):
        if cookie.get(key) not in (None, ""):
            parts.append(f"{attribute}={cookie[key]}")
    if cookie.get("secure"):
        parts.append("secure")
    return "; ".join(parts)
