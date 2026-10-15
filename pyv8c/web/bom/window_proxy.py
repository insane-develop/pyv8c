"""Policy handlers for access to another browsing context's ``Window``.

JavaScript receives V8's native global proxy. These Python handlers answer the
restricted surface when its native access check denies a cross-origin caller.
"""

import pyv8

from ...foundation.arguments import MISSING, dom_string, missing_error, usv_string
from ...idl import standalone
from .utils import deny_cross_origin
from .window import _PROXY_SAFE_NAMES, Window

__all__ = ["WindowProxy"]


class _CrossOriginLocation:
    """The restricted Location exposed by a cross-origin WindowProxy: assigning
    ``location`` works, reading the target URL does not.
    """

    #: Nothing in the document describes this facade, so it states every member it has: the
    #: interceptor is what denies a read, and each attribute below answers by denying.
    __idl_overrides__ = {
        "members": {
            "href": {"setter": "set_href"},
            "origin": {},
            "protocol": {},
            "host": {},
            "hostname": {},
            "port": {},
            "pathname": {},
            "search": {},
            "hash": {},
            "replace": {"length": 1},
        },
        "interface": {
            "name_get": "__v8_name_get__",
            "name_set": "__v8_name_set__",
            "name_del": "__v8_name_del__",
        },
    }

    def __init__(self, proxy):
        self._proxy = proxy

    @property
    def _location(self):
        return self._proxy._child_window.location

    def get_href(self): deny_cross_origin()
    def get_origin(self): deny_cross_origin()
    def get_protocol(self): deny_cross_origin()
    def get_host(self): deny_cross_origin()
    def get_hostname(self): deny_cross_origin()
    def get_port(self): deny_cross_origin()
    def get_pathname(self): deny_cross_origin()
    def get_search(self): deny_cross_origin()
    def get_hash(self): deny_cross_origin()
    def set_href(self, value): return self._location.set_href(value)
    def fn_replace(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error("Location", "replace", 1, 0)
        return self._location.set_href(usv_string(value))

    def __v8_name_get__(self, name):
        if name in ("href", "origin", "protocol", "host", "hostname", "port", "pathname", "search", "hash", "replace"):
            return None
        deny_cross_origin()

    def __v8_name_set__(self, name, value):
        if name == "href":
            self.set_href(value)
            return True
        deny_cross_origin()

    def __v8_name_del__(self, name): deny_cross_origin()


class WindowProxy:
    """Resolve a live frame's permitted cross-origin operations."""

    #: As with the restricted location, the document carries nothing of this facade, so the
    #: class states its whole surface: the safe names, the frame slots, and their controls.
    __idl_overrides__ = {
        "members": {
            "window": {"side_effect": 1},
            "self": {"side_effect": 1},
            "top": {"side_effect": 1},
            "parent": {"side_effect": 1},
            "opener": {"side_effect": 1},
            "frames": {"side_effect": 1},
            "length": {"side_effect": 1},
            "closed": {"side_effect": 1},
            "location": {"setter": "set_location"},
            "close": {},
            "focus": {},
            "blur": {},
            "postMessage": {"length": 2},
        },
        "interface": {
            "name_get": "__v8_name_get__",
            "name_set": "__v8_name_set__",
            "name_query": "__v8_name_query__",
            "name_del": "__v8_name_del__",
            "name_enum": "__v8_name_enum__",
            "index_get": "__v8_index_get__",
            "index_set": "__v8_index_set__",
            "index_del": "__v8_index_del__",
        },
    }

    def __init__(self, parent_window, child_window):
        self._parent_window = parent_window
        #: The frame outlives the Window, which a navigation replaces.
        self._frame = getattr(child_window, "_frame_element", None)
        self._child_context = child_window._services
        self._location = _CrossOriginLocation(self)

    @property
    def _child_window(self):
        frame = self._frame
        if frame is not None and getattr(frame, "_child_services", None) is not None:
            return frame._child_services.window
        return self._child_context.window

    def __getattr__(self, name):
        """What a forwarded member asks of its own window is answered by the child.

        A member the interceptor hands over runs with the proxy as its receiver, so
        ``self._services`` and the rest would look for the child's state here.
        """
        if "_child_context" not in self.__dict__:
            raise AttributeError(name)
        return getattr(self._child_window, name)

    #: The names a cross-origin caller may read; see :mod:`dom.window`.
    _safe_names = _PROXY_SAFE_NAMES

    def __v8_name_get__(self, name):
        if name in self._safe_names:
            return None
        if self._same_origin():
            services = self._child_window._services.realm_services
            if isinstance(name, str):
                getter = getattr(self._child_window, "get_" + name, None)
                if getter is not None:
                    return getter()
                callback = getattr(self._child_window, "fn_" + name, None)
                if callback is not None:
                    # The child's own member keeps the interface's name; the binding
                    # names a Python callback ``fn_<member>`` in the page.
                    installed = services.window_property("get", name)
                    if isinstance(installed, pyv8.JSFunction):
                        return installed
                    return callback
            return services.window_property("get", name)
        deny_cross_origin()

    def __v8_name_set__(self, name, value):
        if name == "location":
            self.set_location(value)
            return True
        if self._same_origin():
            setter = getattr(self._child_window, "set_" + str(name), None)
            if setter is not None:
                setter(value)
            else:
                self._child_window._services.realm_services.window_property("set", name, value)
            return True
        deny_cross_origin()

    def __v8_name_del__(self, name):
        if self._same_origin():
            self._child_window._services.realm_services.window_property("delete", name)
            return True
        deny_cross_origin()

    def __v8_name_query__(self, name):
        if name in self._safe_names:
            return pyv8.V8Attribute.kNone
        if self._same_origin():
            return self._child_window._services.realm_services.window_property("query", name)
        deny_cross_origin()

    def __v8_name_enum__(self): return list(self._safe_names)

    def _same_origin(self):
        return self._accessor()._origin == self._child_window._origin

    def _accessor(self):
        return Window._accessor_window(self._parent_window)

    def __v8_index_get__(self, index):
        frames = self._child_window._frame_windows()
        if 0 <= index < len(frames):
            return self._accessor()._view(frames[index])
        deny_cross_origin()

    def __v8_index_set__(self, index, value): deny_cross_origin()
    def __v8_index_del__(self, index): deny_cross_origin()

    def get_window(self): return self._accessor()._view(self._child_window)
    def get_self(self): return self.get_window()
    def get_frames(self): return self.get_window()
    def get_top(self): return self._accessor()._view(self._child_window._top_target())
    def get_parent(self): return self._accessor()._view(self._child_window._parent_target())
    def get_opener(self): return pyv8.Null
    def get_length(self): return self._child_window.get_length()
    def get_closed(self): return self._child_window.get_closed()
    def get_location(self): return self._child_window.location if self._same_origin() else self._location
    def set_location(self, value): return self._child_window.location.set_href(value)
    def fn_close(self, *_extra): return self._child_window.fn_close()
    def fn_focus(self, *_extra): return self._child_window.fn_focus()
    def fn_blur(self, *_extra): return self._child_window.fn_blur()
    def fn_postMessage(self, message=MISSING, target_origin="/", transfer=MISSING, *_extra):
        from ...foundation.arguments import sequence

        if message is MISSING:
            raise missing_error("Window", "postMessage", 1, 0)
        if transfer is not MISSING:
            target_origin = dom_string(target_origin)
            transfer = sequence(transfer)
        else:
            transfer = ()
        return self._child_window._post_message(self._accessor(), message, target_origin, transfer)


# Neither facade is an interface the document carries, so each states its own description.
standalone(_CrossOriginLocation)
standalone(WindowProxy)
