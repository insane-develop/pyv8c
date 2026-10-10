"""``Keyboard`` and the layout map it hands out.

No keyboard layout is modelled here, so the map is empty rather than fifty invented keys.
"""

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, callback_value, dom_string, missing_error
from .utils import settled

__all__ = ["Keyboard", "KeyboardLayoutMap"]


def _js_call(fn, this, *args):
    if hasattr(fn, "call"):
        return fn.call(this, *args)
    return fn(*args)


@interface('KeyboardLayoutMap', None)
class KeyboardLayoutMap:
    def __init__(self, entries=None, **_):
        self._entries = dict(entries or {})

    def get_size(self): return len(self._entries)
    def fn_get(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('KeyboardLayoutMap', 'get', 1, 0, constructor=False)
        return self._entries.get(dom_string(key), pyv8.Undefined)
    def fn_has(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('KeyboardLayoutMap', 'has', 1, 0, constructor=False)
        return dom_string(key) in self._entries
    def fn_keys(self, *_extra): return list(self._entries)
    def fn_values(self, *_extra): return list(self._entries.values())
    def fn_entries(self, *_extra):
        return [[key, value] for key, value in self._entries.items()]
    def fn_forEach(self, callback=MISSING, this_arg=pyv8.Undefined, *_extra):
        if callback is MISSING:
            raise missing_error('KeyboardLayoutMap', 'forEach', 1, 0, constructor=False)
        callback = callback_value(callback)
        for key, value in list(self._entries.items()):
            _js_call(callback, this_arg, value, key, self)
        return pyv8.Undefined


@interface('Keyboard', None)
class Keyboard:
    def __init__(self, services=None, environment=None, **_):
        self._services = services
        self._environment = environment

    def fn_getLayoutMap(self, *_extra):
        """The machine's layout when the environment collected one, else an empty map."""
        entries = None
        view = getattr(self._environment, "keyboard", None)
        if view is not None:
            entries = view.layout
        return settled(self._services, value=KeyboardLayoutMap(entries=entries))
    def fn_lock(self, keys=None, *_extra):
        """Nothing else can take the keyboard away from the page, so the lock is granted."""
        return settled(self._services, value=None)
    def fn_unlock(self, *_extra): return pyv8.Undefined
