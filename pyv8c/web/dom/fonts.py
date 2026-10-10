"""FontFace and FontFaceSet: the document's font faces.

Every family this DOM renders is available, so a face loads as soon as it is asked for
and ``FontFaceSet.check`` answers true.  No font is ever fetched.
"""
from __future__ import annotations

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dictionary, dom_string, is_nullish, missing_error
from ..page_errors import PageTypeError
from .events import EventTarget

__all__ = ["FontFace", "FontFaceSet"]


def _descriptor(options, name, default):
    value = options.get(name, default) if options else default
    return default if is_nullish(value) else str(value)


@interface('FontFace', None)
class FontFace:
    def __init__(self, family=MISSING, source=MISSING, descriptors=None, *_extra,
                 services=pyv8.Undefined, _realm=None, **_):
        self._services = services if services is not pyv8.Undefined else _realm
        self._family = "" if family is MISSING else str(dom_string(family))
        self._source = "" if source is MISSING else str(source)
        options = dictionary(descriptors, name="FontFaceDescriptors") if descriptors else None
        self._style = _descriptor(options, "style", "normal")
        self._weight = _descriptor(options, "weight", "normal")
        self._stretch = _descriptor(options, "stretch", "normal")
        self._unicode_range = _descriptor(options, "unicodeRange", "U+0-10FFFF")
        self._ascent_override = _descriptor(options, "ascentOverride", "normal")
        self._feature_settings = _descriptor(options, "featureSettings", "normal")
        self._variation_settings = _descriptor(options, "variationSettings", "normal")
        self._display = _descriptor(options, "display", "auto")
        self._status = "unloaded"
        self._loaded = None
        self._resolve = None

    def get_family(self): return self._family
    def get_style(self): return self._style
    def set_style(self, value): self._style = str(dom_string(value))
    def get_weight(self): return self._weight
    def set_weight(self, value): self._weight = str(dom_string(value))
    def get_stretch(self): return self._stretch
    def set_stretch(self, value): self._stretch = str(dom_string(value))
    def get_ascentOverride(self): return self._ascent_override
    def set_ascentOverride(self, value):
        self._ascent_override = str(dom_string(value))
    def get_unicodeRange(self): return self._unicode_range
    def set_unicodeRange(self, value): self._unicode_range = str(dom_string(value))
    def get_featureSettings(self): return self._feature_settings
    def set_featureSettings(self, value): self._feature_settings = str(dom_string(value))
    def get_variationSettings(self): return self._variation_settings
    def set_variationSettings(self, value): self._variation_settings = str(dom_string(value))
    def get_display(self): return self._display
    def set_display(self, value): self._display = str(dom_string(value))
    def get_status(self): return self._status

    def get_loaded(self):
        if self._loaded is None:
            if self._services is None:
                return pyv8.Undefined
            self._loaded, self._resolve, _reject = self._services.realm_services.make_promise()
        return self._loaded

    def fn_load(self, *_extra):
        promise = self.get_loaded()
        if self._resolve is not None and self._status == "unloaded":
            self._status = "loaded"
            self._resolve(self)
        return promise


@interface('FontFaceSet', 'EventTarget')
class FontFaceSet(EventTarget):
    #: ``keys``/``values``/``entries`` and ``Symbol.iterator`` hand out pairs from here.
    __idl_overrides__ = {
        "interface": {
            "iterate": "__v8_iterate__",
            "length": 0,
        }
    }

    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services or _realm, **kw)
        self._faces = []
        self._ready = None

    def _face_argument(self, value, member):
        if not isinstance(value, FontFace):
            raise PageTypeError(f"Failed to execute '{member}' on 'FontFaceSet': "
                              "parameter 1 is not of type 'FontFace'.")
        return value

    def get_status(self): return "loaded"
    def get_size(self): return len(self._faces)
    def get_ready(self):
        if self._ready is None:
            self._ready = (self._services.realm_services.promise_resolve(self)
                           if self._services is not None else pyv8.Undefined)
        return self._ready
    def get_onloading(self): return self._get_handler("loading")
    def set_onloading(self, value): self._set_handler("loading", value)
    def get_onloadingdone(self): return self._get_handler("loadingdone")
    def set_onloadingdone(self, value): self._set_handler("loadingdone", value)
    def get_onloadingerror(self): return self._get_handler("loadingerror")
    def set_onloadingerror(self, value): self._set_handler("loadingerror", value)

    def fn_add(self, face=MISSING, *_extra):
        if face is MISSING:
            raise missing_error('FontFaceSet', 'add', 1, 0, constructor=False)
        face = self._face_argument(face, 'add')
        if face not in self._faces:
            self._faces.append(face)

    def fn_delete(self, face=MISSING, *_extra):
        if face is MISSING:
            raise missing_error('FontFaceSet', 'delete', 1, 0, constructor=False)
        face = self._face_argument(face, 'delete')
        if face in self._faces:
            self._faces.remove(face)
            return True
        return False

    def fn_clear(self, *_extra):
        self._faces.clear()

    def fn_has(self, face=MISSING, *_extra):
        if face is MISSING:
            raise missing_error('FontFaceSet', 'has', 1, 0, constructor=False)
        return self._face_argument(face, 'has') in self._faces

    def fn_check(self, font=MISSING, *_extra):
        if font is MISSING:
            raise missing_error('FontFaceSet', 'check', 1, 0, constructor=False)
        dom_string(font)
        return True

    def fn_load(self, font=MISSING, *_extra):
        if font is MISSING:
            raise missing_error('FontFaceSet', 'load', 1, 0, constructor=False)
        dom_string(font)
        if self._services is None:
            return pyv8.Undefined
        return self._services.realm_services.promise_resolve([])

    def fn_forEach(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error('FontFaceSet', 'forEach', 1, 0, constructor=False)
        for face in list(self._faces):
            callback(face, face, self)

    def __v8_iterate__(self, index):
        if not 0 <= index < len(self._faces):
            return None
        face = self._faces[index]
        return (face, face)
