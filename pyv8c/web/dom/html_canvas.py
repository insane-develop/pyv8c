"""``HTMLCanvasElement`` and its 2D rendering context.

The optional Pillow dependency backs the drawing surface; without it the
context still exposes the full API and simply records nothing.
"""

import base64
import io
import math
import re

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from .events import EventTarget
from .html_elements import HTMLElement
from .webgl import WebGL2RenderingContext, WebGLRenderingContext


#: ``textBaseline`` accepts these spellings; any other value leaves the state untouched.
_TEXT_BASELINES = frozenset(("alphabetic", "bottom", "hanging", "ideographic", "middle", "top"))


#: The context kinds a canvas can answer for; ``experimental-webgl`` is the older spelling.
_CONTEXT_KINDS = ("2d", "webgl", "webgl2")


def _requested_context(kind):
    """The context kind a page asked for, or ``None`` when a canvas has none to give."""
    requested = str(kind).lower()
    if requested == "experimental-webgl": return "webgl"
    return requested if requested in _CONTEXT_KINDS else None


@interface('TextMetrics', None)
class TextMetrics:
    """What ``measureText`` returns.  Only ``width`` is measured; the boxes a
    caller rarely reads report that same run's edges, as Chrome's do."""

    def __init__(self, width=0.0, **_):
        self._width = float(width)

    def get_width(self): return self._width
    def get_actualBoundingBoxLeft(self): return 0.0
    def get_actualBoundingBoxRight(self): return self._width
    def get_actualBoundingBoxAscent(self): return 0.0
    def get_actualBoundingBoxDescent(self): return 0.0
    def get_fontBoundingBoxAscent(self): return 0.0
    def get_fontBoundingBoxDescent(self): return 0.0
    def get_emHeightAscent(self): return 0.0
    def get_emHeightDescent(self): return 0.0
    def get_hangingBaseline(self): return 0.0
    def get_alphabeticBaseline(self): return 0.0
    def get_ideographicBaseline(self): return 0.0


def _canvas_color(value):
    value = str(value or "").strip().lower()
    named = {"red": (255, 0, 0, 255), "green": (0, 128, 0, 255), "blue": (0, 0, 255, 255),
             "black": (0, 0, 0, 255), "white": (255, 255, 255, 255), "transparent": (0, 0, 0, 0)}
    if value in named: return named[value]
    if value.startswith("#"):
        h = value[1:]
        if len(h) in (3, 4): h = ''.join(c * 2 for c in h)
        if len(h) == 6: h += "ff"
        try: return tuple(int(h[i:i+2], 16) for i in (0, 2, 4, 6))
        except ValueError: return (0, 0, 0, 255)
    m = re.match(r"rgba?\(([^)]+)\)", value)
    if m:
        p = [x.strip() for x in m.group(1).split(',')]
        try:
            rgb = [round(float(x[:-1]) * 2.55) if x.endswith('%') else int(float(x)) for x in p[:3]]
            if len(p) > 3:
                a = float(p[3]) * 255 if not p[3].endswith('%') else float(p[3][:-1]) * 2.55
            else:
                a = 255
            return tuple(max(0, min(255, int(x))) for x in (*rgb, a))
        except (ValueError, IndexError): pass
    return (0, 0, 0, 255)


@interface('ImageData', None)
class ImageData:
    """A rectangle of RGBA pixels, as ``getImageData`` hands it back."""

    def __init__(self, pixels=b"", width=0, height=0, **_):
        self._width, self._height = int(width), int(height)
        self._data = pyv8.JSTypeArray(list(pixels), pyv8.ArrayType.Uint8ClampedArray)

    def get_width(self): return self._width
    def get_height(self): return self._height
    def get_data(self): return self._data
    def get_colorSpace(self): return "srgb"


@interface('CanvasRenderingContext2D', None)
class CanvasRenderingContext2D:
    #: Chrome's ``2d`` context also carries ``lineWidth``, ``globalAlpha``, ``miterLimit``,
    #: ``lineDashOffset``, ``imageSmoothingEnabled`` and the three ``shadow*``; the baseline is
    #: missing that mixin, and the engine rejects a declaration that contradicts it, so the fix
    #: is a regenerated document (``tools/extract_idl.py``, needs ``CHROMIUM_ROOT``).

    def __init__(self, canvas):
        self._canvas = canvas; self._fill = "#000000"; self._stroke = "#000000"; self._font = "10px sans-serif"
        self._baseline = "alphabetic"
        self._path = []; self._stack = []
        try:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (canvas._width, canvas._height), (0, 0, 0, 0))
            self._draw = ImageDraw.Draw(self._image)
        except ImportError: self._image = self._draw = None
    def _reset(self):
        if self._image is not None:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (self._canvas._width, self._canvas._height), (0, 0, 0, 0))
            self._draw = ImageDraw.Draw(self._image)
    def get_canvas(self): return self._canvas
    def get_fillStyle(self): return self._fill
    def set_fillStyle(self, v): self._fill = str(v)
    def get_strokeStyle(self): return self._stroke
    def set_strokeStyle(self, v): self._stroke = str(v)
    def get_font(self): return self._font
    def set_font(self, v): self._font = str(v)
    def get_textBaseline(self): return self._baseline
    def set_textBaseline(self, value):
        value = str(dom_string(value))
        if value in _TEXT_BASELINES:
            self._baseline = value
    def _font_obj(self):
        if self._image is None: return None
        from PIL import ImageFont
        m = re.search(r"([0-9.]+)\s*(px|pt)", self._font); size = float(m.group(1)) if m else 10
        if m and m.group(2) == 'pt': size *= 96 / 72
        family = self._font[m.end():].strip().split(',')[0].strip('"\' ') if m else ''
        candidates = [family, "arial", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
        for f in candidates:
            try:
                if f: return ImageFont.truetype(f, max(1, int(size)))
            except OSError: pass
        return ImageFont.load_default()
    def fn_getImageData(self, x=MISSING, y=MISSING, sw=MISSING, sh=MISSING, *_extra):
        """The pixels of one rectangle, transparent black outside the canvas."""
        for position, value in enumerate((x, y, sw, sh)):
            if value is MISSING:
                raise missing_error('CanvasRenderingContext2D', 'getImageData', 4, position,
                                    constructor=False)
        left, top = int(math.floor(float(x))), int(math.floor(float(y)))
        width, height = int(float(sw)), int(float(sh))
        if width < 0 or height < 0:
            from ..page_errors import IndexSizeError
            raise IndexSizeError(
                "Failed to execute 'getImageData' on 'CanvasRenderingContext2D':"
                " The source width or height is negative.")
        pixels = (bytes(4 * width * height) if self._image is None else
                  self._image.crop((left, top, left + width, top + height)).tobytes())
        return ImageData(pixels, width, height)

    def fn_fillRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw:
            self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), fill=_canvas_color(self._fill))
    def fn_clearRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw: self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), fill=(0,0,0,0))
    def fn_fillText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw.text(
                (float(x), float(y)), str(text), font=self._font_obj(),
                fill=_canvas_color(self._fill), anchor='ls',
            )
    def fn_strokeText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw.text(
                (float(x), float(y)), str(text), font=self._font_obj(),
                fill=None, stroke_width=1, stroke_fill=_canvas_color(self._stroke), anchor='ls',
            )
    def fn_beginPath(self, *_): self._path = []
    def fn_moveTo(self, x=0, y=0, *_): self._path.append([(float(x), float(y))])
    def fn_lineTo(self, x=0, y=0, *_):
        if not self._path: self.fn_moveTo(x, y)
        else: self._path[-1].append((float(x), float(y)))
    def fn_arc(self, x=0, y=0, r=0, start=0, end=0, anticlockwise=False, *_):
        n = max(8, int(abs(float(end) - float(start)) * 24))
        pts = [
            (float(x) + float(r) * math.cos(float(start) + (float(end) - float(start)) * i / n),
             float(y) + float(r) * math.sin(float(start) + (float(end) - float(start)) * i / n))
            for i in range(n + 1)
        ]
        self._path.append(pts)
    def fn_stroke(self, *_):
        if self._draw:
            for p in self._path:
                if len(p) > 1: self._draw.line(p, fill=_canvas_color(self._stroke), width=1)
    def fn_strokeRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw:
            self._draw.rectangle(
                (float(x), float(y), float(x) + float(w), float(y) + float(h)),
                outline=_canvas_color(self._stroke), width=1,
            )
    def fn_measureText(self, text='', *_):
        width = (self._draw.textlength(str(text), font=self._font_obj())
                 if self._draw else len(str(text)) * 10)
        return TextMetrics(width)
    def fn_save(self, *_): self._stack.append((self._fill, self._stroke, self._font, self._baseline))
    def fn_restore(self, *_):
        if self._stack: self._fill, self._stroke, self._font, self._baseline = self._stack.pop()


@interface('HTMLCanvasElement', 'HTMLElement')
class HTMLCanvasElement(HTMLElement):
    def __init__(self, tag_name="canvas", services=None, **kw):
        super().__init__(tag_name, services=services, **kw); self._width=300; self._height=150; self._context2d=None
        self._context_kind=None; self._webgl=None; self._webgl2=None
    def _resize(self):
        if self._context2d: self._context2d._reset()
    def get_width(self): return self._width
    def set_width(self, v): self._width=max(0, int(float(v))); self.attributes['width']=str(self._width); self._resize()
    def get_height(self): return self._height
    def set_height(self, v):
        self._height=max(0, int(float(v))); self.attributes['height']=str(self._height); self._resize()
    def fn_getContext(self, kind='', *_):
        requested = _requested_context(kind)
        if requested is None: return pyv8.Null
        # The first context a canvas answers for fixes its type, as it does in Chrome.
        if self._context_kind not in (None, requested): return pyv8.Null
        self._context_kind = requested
        if requested == '2d':
            if self._context2d is None: self._context2d=CanvasRenderingContext2D(self)
            return self._context2d
        if requested == 'webgl2':
            if self._webgl2 is None: self._webgl2=WebGL2RenderingContext(self)
            return self._webgl2
        if self._webgl is None: self._webgl=WebGLRenderingContext(self)
        return self._webgl
    def fn_toDataURL(self, kind='image/png', *_):
        surface = self._context2d
        if surface is None:
            # A WebGL canvas draws nothing, so its pixels read back transparent.
            surface = (CanvasRenderingContext2D(self)
                       if self._context_kind in ('webgl', 'webgl2') else None)
            if surface is None:
                self.fn_getContext('2d'); surface = self._context2d
        out=io.BytesIO(); surface._image.save(out, format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii')


@interface('OffscreenCanvasRenderingContext2D', 'CanvasRenderingContext2D')
class OffscreenCanvasRenderingContext2D(CanvasRenderingContext2D):
    """The 2D context an ``OffscreenCanvas`` hands out: the same surface, no element."""


@interface('OffscreenCanvas', 'EventTarget')
class OffscreenCanvas(EventTarget):
    """A canvas with no element behind it: the page keeps the surface for itself."""

    __idl_overrides__ = {
        "interface": {
            "length": 2
        }
    }

    def __init__(self, width=MISSING, height=MISSING, *args, services=pyv8.Undefined,
                 _realm=None, **kwargs):
        if services is pyv8.Undefined:
            services = None
        super().__init__(*args, services=services or _realm, **kwargs)
        if width is MISSING:
            raise missing_error('OffscreenCanvas', 'constructor', 2, 0, constructor=True)
        if height is MISSING:
            raise missing_error('OffscreenCanvas', 'constructor', 2, 1, constructor=True)
        self._width = max(0, int(float(width)))
        self._height = max(0, int(float(height)))
        self._context2d = self._webgl = self._webgl2 = self._context_kind = None

    def _resize(self):
        if self._context2d: self._context2d._reset()
    def get_width(self): return self._width
    def set_width(self, value): self._width = max(0, int(float(value))); self._resize()
    def get_height(self): return self._height
    def set_height(self, value): self._height = max(0, int(float(value))); self._resize()
    def get_oncontextlost(self): return self._get_handler("oncontextlost")
    def set_oncontextlost(self, value): self._set_handler("oncontextlost", value)
    def get_oncontextrestored(self): return self._get_handler("oncontextrestored")
    def set_oncontextrestored(self, value): self._set_handler("oncontextrestored", value)

    def fn_getContext(self, kind='', options=None, *_):
        requested = _requested_context(kind)
        if requested is None:
            return pyv8.Null
        if self._context_kind not in (None, requested): return pyv8.Null
        self._context_kind = requested
        if requested == '2d':
            if self._context2d is None: self._context2d = OffscreenCanvasRenderingContext2D(self)
            return self._context2d
        if requested == 'webgl2':
            if self._webgl2 is None: self._webgl2 = WebGL2RenderingContext(self)
            return self._webgl2
        if self._webgl is None: self._webgl = WebGLRenderingContext(self)
        return self._webgl

    def fn_convertToBlob(self, options=None, *_):
        """The surface as a PNG, in the promise a caller attaches to."""
        surface = self._context2d
        if surface is None:
            self.fn_getContext('2d'); surface = self._context2d
        out = io.BytesIO()
        if surface._image is not None:
            surface._image.save(out, format='PNG')
        from ..fetch.blob import Blob
        return self._promise(Blob([out.getvalue()], {"type": "image/png"}))

    def fn_transferToImageBitmap(self, *_):
        """No ``ImageBitmap`` here, so the call answers with nothing to hand over."""
        return None
    def fn_clearElementGeometry(self, *_): return None
    def fn_getElementTransform(self, *_): return None
    def fn_updateElementGeometry(self, *_): return None

    def _promise(self, value):
        realm = getattr(getattr(self, "_services", None), "realm_services", None)
        if realm is None:
            return None
        promise, resolve, _reject = realm.make_promise()
        resolve(value)
        return promise
