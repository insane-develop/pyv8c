"""``URL`` and the live ``URLSearchParams`` view it hands out.

``URL.searchParams`` is bound to its owner through ``_url``, which writes every
mutation back with ``_set_search_from_params``.
"""

from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from ...engine.features import LazyValueStore
from ...idl import interface
from ...foundation.arguments import (
    MISSING,
    callback_value,
    dictionary,
    dom_string,
    interface_value,
    is_nullish,
    missing_error,
    sequence,
    usv_string,
)
from ...foundation.arguments import IdlArgumentError
from ..page_errors import InvalidStateError, PageTypeError, PageURIError
from ...foundation.urls import SPECIAL_SCHEMES, credentials_netloc, form_urlencoded
from ..fetch.blob import Blob

__all__ = ["URL", "URLSearchParams"]


#: C0 controls and spaces the parser strips; tabs/newlines drop anywhere.
_URL_TRIM = "".join(chr(code) for code in range(0x21))
_URL_DROP = str.maketrans("", "", "\t\n\r")


def _optional_value(value):
    """The ``delete``/``has`` second argument; ``None`` when it was left out."""
    if value is MISSING or value is pyv8.Undefined:
        return None
    return str(dom_string(value))


def _js_call(fn, this, *args):
    if hasattr(fn, "call"):
        return fn.call(this, *args)
    return fn(*args)


@interface('URLSearchParams', None)
class URLSearchParams:

    #: ``keys``/``values``/``entries`` and ``Symbol.iterator`` hand out pairs from here.
    __idl_overrides__ = {"interface": {"iterate": "__v8_iterate__"}}

    def __init__(self, init='', *_extra, _url=None, **_):
        if init is pyv8.Undefined:
            init = ''
        self._url = _url
        if isinstance(init, URLSearchParams):
            init._refresh()
            self._items = list(init._items)
            return
        iterable = isinstance(init, (list, tuple))
        iterator = MISSING
        if isinstance(init, pyv8.JSObject):
            iterator = init._iterator_method()
            iterable = not is_nullish(iterator)
        if iterable:
            def convert_pair(entry):
                pair = sequence(entry, usv_string)
                if len(pair) != 2:
                    # Chromium words the constructor's failure twice over.
                    raise PageTypeError(
                        "Failed to construct 'URLSearchParams': Failed to construct "
                        "'URLSearchParams': Sequence initializer must only contain "
                        "pair elements")
                return tuple(pair)
            self._items = sequence(init, convert_pair, iterator_method=iterator)
        elif isinstance(init, (dict, pyv8.JSObject)):
            self._items = [(usv_string(key), usv_string(value)) for key, value in dictionary(init).items()]
        else:
            self._items = self._parse(usv_string(init))

    @staticmethod
    def _parse(text):
        """The entries *text* spells; only the first ``?`` is a delimiter."""
        from urllib.parse import parse_qsl
        query = text[1:] if text.startswith("?") else text
        return list(parse_qsl(query, keep_blank_values=True))

    def _refresh(self):
        if self._url is not None:
            self._items = self._parse(urlsplit(self._url.href).query)

    def _commit(self):
        if self._url is not None:
            self._url._set_search_from_params(form_urlencoded(self._items))

    def fn_append(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'append', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('URLSearchParams', 'append', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self._refresh(); self._items.append((str(name), str(value))); self._commit()

    def fn_delete(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'delete', 1, 0, constructor=False)
        name = str(dom_string(name))
        value = _optional_value(value)
        self._refresh()
        if value is None:
            self._items = [(key, item) for key, item in self._items if key != name]
        else:
            self._items = [(key, item) for key, item in self._items
                           if key != name or item != value]
        self._commit()

    def fn_get(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'get', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name)
        for key, value in self._items:
            if key == name: return value
        return pyv8.Null

    def fn_getAll(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'getAll', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); return [value for key, value in self._items if key == name]

    def fn_has(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'has', 1, 0, constructor=False)
        name = str(dom_string(name))
        value = _optional_value(value)
        self._refresh()
        return any(key == name and (value is None or item == value)
                   for key, item in self._items)

    def fn_set(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'set', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('URLSearchParams', 'set', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self._refresh(); name, value = str(name), str(value)
        replaced = False; result = []
        for key, old_value in self._items:
            if key == name:
                if not replaced: result.append((name, value)); replaced = True
            else: result.append((key, old_value))
        if not replaced: result.append((name, value))
        self._items = result; self._commit()

    def fn_sort(self, *_extra):
        # Names compare by UTF-16 code unit, not by Python's code point order.
        self._refresh()
        self._items.sort(key=lambda item: item[0].encode("utf-16-be"))
        self._commit()

    def get_size(self):
        self._refresh(); return len(self._items)

    def __v8_iterate__(self, index):
        """The pair at *index*, re-read so a view stays live; None ends the walk."""
        self._refresh()
        if index >= len(self._items):
            return None
        return self._items[index]

    def fn_toString(self, *_extra):
        self._refresh()
        return form_urlencoded(self._items)

    def fn_forEach(self, callback=MISSING, this_arg=pyv8.Undefined, *_extra):
        if callback is MISSING:
            raise missing_error('URLSearchParams', 'forEach', 1, 0, constructor=False)
        callback = callback_value(callback)
        # Indexed, not over a copy: a removal skips the entry that takes its place.
        index = 0
        while True:
            self._refresh()
            if index >= len(self._items):
                return
            name, value = self._items[index]
            _js_call(callback, this_arg, value, name, self)
            index += 1


def _url_values(url):
    """The sub-objects a URL hands out, built on first read."""
    return LazyValueStore({
        "searchParams": lambda: URLSearchParams(url.get_search(), _url=url),
    })


@interface('URL', None)
class URL:
    def __init__(self, url=MISSING, base=pyv8.Undefined, *_extra, _realm=None, **_):
        if url is MISSING:
            raise missing_error('URL', 'constructor', 1, 0, constructor=True)
        url = usv_string(url)
        base = None if base is pyv8.Undefined else usv_string(base)
        self._realm = _realm
        try:
            if base is not None:
                base = self._resolve(base)
            self.href = self._resolve(url, base)
        except (PageTypeError, IdlArgumentError) as exc:
            raise PageTypeError("Failed to construct 'URL': Invalid URL") from exc
        self._values = _url_values(self)

    @staticmethod
    def _resolve(url, base=None):
        """The absolute form of ``url`` with a browser's whitespace handling:
        trim and drop, then percent-encode every space left, in the authority as
        much as the path.  Reading the port is what rejects an invalid one.
        """
        raw = str(url).translate(_URL_DROP).strip(_URL_TRIM).replace(" ", "%20")
        if not raw:
            raise PageTypeError("Invalid URL")
        value = raw if base is None else urljoin(str(base), raw)
        try:
            parts = urlsplit(value)
            if not parts.scheme or (parts.scheme in SPECIAL_SCHEMES and not parts.netloc):
                raise PageURIError("Invalid URL")
            _ = parts.port
        except (ValueError, PageURIError) as exc:
            raise PageTypeError("Invalid URL") from exc
        return urlunsplit(parts)

    def _parts(self): return urlsplit(self.href)
    def _replace(self, *, scheme=None, netloc=None, path=None, query=None, fragment=None):
        parts = self._parts()
        self.href = urlunsplit((scheme if scheme is not None else parts.scheme,
                                netloc if netloc is not None else parts.netloc,
                                path if path is not None else parts.path,
                                query if query is not None else parts.query,
                                fragment if fragment is not None else parts.fragment))

    def _set_search_from_params(self, query): self._replace(query=str(query))
    def get_href(self): return self.href
    def set_href(self, value):
        value = dom_string(value)
        self.href = self._resolve(value, self.href)
    def get_origin(self):
        parts = self._parts()
        if parts.scheme not in SPECIAL_SCHEMES or not parts.netloc:
            return "null"
        host = parts.hostname or ""
        if parts.port is not None:
            host += ":" + str(parts.port)
        return f"{parts.scheme}://{host}"
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ""
    def set_protocol(self, value):
        value = dom_string(value)
        self._replace(scheme=str(value).rstrip(':'))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        value = dom_string(value)
        self._set_credentials(username=str(value), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        value = dom_string(value)
        self._set_credentials(username=self.get_username(), password=str(value))
    def _set_credentials(self, *, username, password):
        parts = self._parts()
        self._replace(netloc=credentials_netloc(parts.hostname or "", parts.port, username, password))
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value):
        value = dom_string(value)
        self._replace(netloc=str(value))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        value = dom_string(value)
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._replace(netloc=credentials + str(value) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        value = dom_string(value)
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        self._replace(netloc=credentials + (parts.hostname or "") + ((":" + str(value)) if str(value) else ""))
    def get_pathname(self):
        path = self._parts().path
        return path or ("/" if self._parts().netloc else "")
    def set_pathname(self, value):
        value = dom_string(value)
        self._replace(path=str(value) or '/')
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value):
        value = dom_string(value)
        self._replace(query=str(value).lstrip('?'))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value):
        value = dom_string(value)
        self._replace(fragment=str(value).lstrip('#'))
    def get_searchParams(self): return self._values.value("searchParams")
    def fn_toString(self, *_extra):
        return self.href
    def fn_toJSON(self, *_extra):
        return self.href

    @staticmethod
    def fn_canParse(url=MISSING, base=pyv8.Undefined, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'canParse', 1, 0, constructor=False)
        url = dom_string(url)
        if base is not pyv8.Undefined:
            base = dom_string(base)
        try: URL(url, base)
        except (PageURIError, TypeError, ValueError): return False
        return True

    @staticmethod
    def fn_parse(url=MISSING, base=pyv8.Undefined, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'parse', 1, 0, constructor=False)
        url = dom_string(url)
        if base is not pyv8.Undefined:
            base = dom_string(base)
        try: return URL(url, base)
        except (PageURIError, TypeError, ValueError): return pyv8.Null

    @staticmethod
    def fn_createObjectURL(_realm, blob=MISSING, *_extra):
        if blob is MISSING:
            raise missing_error('URL', 'createObjectURL', 1, 0, constructor=False)
        blob = interface_value(blob, 'Blob', 'URL', 'createObjectURL', 1, nullable=False)
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        if not isinstance(blob, Blob): raise PageTypeError("The object is not a Blob")
        return services.context_services.create_blob_url(blob, realm=services.realm_services)

    @staticmethod
    def fn_revokeObjectURL(_realm, url=MISSING, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'revokeObjectURL', 1, 0, constructor=False)
        url = dom_string(url)
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        services.context_services.revoke_blob_url(str(url))

    __idl_overrides__ = {
        "interface": {
            "length": 1
        }
    }
