"""``FormData``: the name/value pairs a form would send.

Entries keep the order they were added in and a name may repeat, which is what ``get``
(first one), ``getAll`` and ``set`` (replace the first, drop the rest) are defined over.
A value is a string or a ``Blob``; a filename with a blob would need ``File``, which this
build does not implement yet, so the blob is stored as it is.
"""

import pyv8

from ...idl import interface
from ...foundation.arguments import (MISSING, callback_value, dom_string, missing_error,
                                     usv_string)
from ..dom.mixins import ReadOnlyIndexedPropertyMixin
from .blob import Blob


def _js_call(fn, this, *args):
    if hasattr(fn, "call"):
        return fn.call(this, *args)
    return fn(*args)


@interface('FormData', None)
class FormData(ReadOnlyIndexedPropertyMixin):
    def __init__(self, *_extra, **_):
        self._items = []

    #: A value is a string unless it is a blob, which a form sends as it stands.
    def _value(self, value):
        if isinstance(value, Blob) or isinstance(value, pyv8.JSObject):
            return value
        return usv_string(value)

    def _name(self, name):
        return str(dom_string(name))

    def _indices(self, name):
        return [index for index, item in enumerate(self._items) if item[0] == name]

    def _indexed_items(self):
        return list(self._items)

    def fn_append(self, name=MISSING, value=MISSING, filename=pyv8.Undefined, *_extra):
        if name is MISSING:
            raise missing_error('FormData', 'append', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('FormData', 'append', 2, 1, constructor=False)
        self._items.append((self._name(name), self._value(value)))
        return pyv8.Undefined

    def fn_set(self, name=MISSING, value=MISSING, filename=pyv8.Undefined, *_extra):
        if name is MISSING:
            raise missing_error('FormData', 'set', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('FormData', 'set', 2, 1, constructor=False)
        key, stored = self._name(name), self._value(value)
        found = self._indices(key)
        if not found:
            self._items.append((key, stored))
            return pyv8.Undefined
        first = found[0]
        self._items[:] = [item for index, item in enumerate(self._items)
                          if index not in found[1:]]
        self._items[first] = (key, stored)
        return pyv8.Undefined

    def fn_delete(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('FormData', 'delete', 1, 0, constructor=False)
        key = self._name(name)
        self._items[:] = [item for item in self._items if item[0] != key]
        return pyv8.Undefined

    def fn_get(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('FormData', 'get', 1, 0, constructor=False)
        found = self._indices(self._name(name))
        return self._items[found[0]][1] if found else pyv8.Null

    def fn_getAll(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('FormData', 'getAll', 1, 0, constructor=False)
        key = self._name(name)
        return [value for item_name, value in self._items if item_name == key]

    def fn_has(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('FormData', 'has', 1, 0, constructor=False)
        return bool(self._indices(self._name(name)))

    def fn_keys(self, *_extra):
        return [name for name, _ in self._items]

    def fn_values(self, *_extra):
        return [value for _, value in self._items]

    def fn_entries(self, *_extra):
        return [(name, value) for name, value in self._items]

    def fn_forEach(self, callback=MISSING, this_arg=pyv8.Undefined, *_extra):
        if callback is MISSING:
            raise missing_error('FormData', 'forEach', 1, 0, constructor=False)
        callback = callback_value(callback)
        index = 0
        while index < len(self._items):
            name, value = self._items[index]
            _js_call(callback, this_arg, value, name, self)
            index += 1
        return pyv8.Undefined

__all__ = ["FormData"]
