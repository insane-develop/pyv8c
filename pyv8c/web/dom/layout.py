"""Small CSS cascade, font measurement and layout facade for DOM geometry.

The property tables (initial values, inherited properties) live in :mod:`dom.css`.

Boxes are the *border* boxes in viewport coordinates, which is what
``getBoundingClientRect`` reports and what the hit test walks.  Margins place a box
inside its containing block, ``position: relative`` slides it afterwards, and
``position: absolute`` takes it out of flow and measures it against the padding box
of the nearest positioned ancestor.
"""
import os
import re
from dataclasses import dataclass, field

try:
    from PIL import ImageFont
except Exception:  # optional dependency  # pragma: no cover
    ImageFont = None

from ..page_errors import PageSyntaxError
from .character_data import Text
from .css import (
    INHERITED_PROPERTIES,
    CSSStyleDeclaration,
    initial_values_for,
    parse_declarations,
    stylesheet_source,
)
from .html_elements import Element
from .selectors import matches


def _number(value, base=0.0):
    match = re.match(r"^(-?(?:\d+(?:\.\d*)?|\.\d+))", str(value).strip())
    return float(match.group(1)) if match else base


def _px(value, reference=0.0):
    value = str(value).strip().lower()
    if value.endswith("px"): return _number(value)
    if value.endswith("%"): return reference * _number(value) / 100.0
    if value in ("0", "0px"): return 0.0
    return _number(value, 0.0)

_FONT_ROOTS = (
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation2",
)
_FONT_FALLBACKS = ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")


#: A page measures text far more often than a font directory changes, so the listing,
#: the resolved paths and the loaded handles are each read once per process.
_FONT_FILES: dict[str, tuple[str, ...]] = {}
_FONT_PATHS: dict[tuple, list[str]] = {}
_FONT_HANDLES: dict[tuple, object] = {}


def _stem(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).lower()


def _font_files(root):
    if root not in _FONT_FILES:
        try:
            _FONT_FILES[root] = tuple(os.listdir(root)) if os.path.isdir(root) else ()
        except OSError:
            _FONT_FILES[root] = ()
    return _FONT_FILES[root]


def _font(path_candidates, size, weight="400", italic=False):
    if ImageFont is None: return None
    for path in path_candidates:
        key = (path, max(1, int(round(size))))
        handle = _FONT_HANDLES.get(key)
        if handle is None:
            try: handle = _FONT_HANDLES[key] = ImageFont.truetype(path, key[1])
            except Exception: continue
        return handle
    return None


def _font_candidates(families):
    key = tuple(families)
    if key not in _FONT_PATHS:
        names = [
            name for family in families
            for root in _FONT_ROOTS for name in _font_files(root)
            if name.lower().endswith((".ttf", ".otf")) and _stem(family) in _stem(name)
        ]
        names.extend(_FONT_FALLBACKS)
        _FONT_PATHS[key] = [
            os.path.join(root, name) for root in _FONT_ROOTS for name in names
            if os.path.exists(os.path.join(root, name))
        ]
    return _FONT_PATHS[key]


def text_width(text, style):
    size = _px(style.get("font-size", "16px"), 16) or 16
    families = [x.strip().strip('"\'') for x in style.get("font-family", "sans-serif").split(",")]
    font = _font(
        _font_candidates(families), size,
        style.get("font-weight", "400"), style.get("font-style") == "italic",
    )
    if font is not None:
        try: width = font.getlength(text)
        except AttributeError: width = font.getbbox(text)[2]
    else:
        width = sum(size * (0.34 if ch in "ilI.,' !" else 0.56 if ord(ch) < 128 else 1.0) for ch in text)
    if style.get("letter-spacing", "normal") != "normal": width += max(0, len(text) - 1) * _px(style["letter-spacing"])
    return width


def _inline_declarations(element):
    return {name: value for name, value, _ in CSSStyleDeclaration(element=element)._declarations()}


def _selector_matches(element, selector):
    """Match one stylesheet selector; unsupported syntax simply never matches."""
    try:
        return matches(element, selector)
    except PageSyntaxError:
        return False


def document_rules(document):
    """The document's ``<style>`` rules as ``(selectors, declarations)`` pairs.

    Reading the sheet walks the document and every layout query re-reads it, so
    the result is cached on the document, keyed on ``style_version`` (bumped
    whenever a ``<style>`` is added, removed or edited).
    """
    if document is None:
        return ()
    version = getattr(document, "style_version", 0)
    cached = getattr(document, "_rules_cache", None)
    if cached is not None and cached[0] == version:
        return cached[1]
    rules = _parse_style_elements(document)
    document._rules_cache = (version, rules)
    return rules


def _parse_style_elements(document):
    rules = []
    for node in document._descendants():
        source = stylesheet_source(node)
        if not source:
            continue
        for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
            rules.append((selector.split(","), parse_declarations(block)))
    return tuple(rules)


def _cascade(element, rules):
    values = initial_values_for(element)
    parent = getattr(element, "parentElement", None)
    if parent is not None:
        inherited = _cascade(parent, rules)
        # Inherit every inherited property, not only those with an initial value here.
        values.update({
            name: inherited.get(name, values.get(name, ""))
            for name in INHERITED_PROPERTIES
        })
    for selectors, declarations in rules:
        if not any(_selector_matches(element, part) for part in selectors):
            continue
        for name, value, _priority in declarations:
            values[name] = value
    values.update(_inline_declarations(element))
    return values


def computed_style(element):
    return _cascade(element, document_rules(getattr(element, "ownerDocument", None)))


@dataclass
class Box:
    """One element's boxes, in viewport coordinates.

    ``left``/``top``/``width``/``height`` are the border box, and the padding and
    border widths say where the padding box and content box sit inside it.
    """
    width: float = 0
    height: float = 0
    left: float = 0
    top: float = 0
    padding: dict = field(default_factory=dict)
    border: dict = field(default_factory=dict)
    margin: dict = field(default_factory=dict)

    @property
    def padding_box_left(self): return self.left + self.border.get("left", 0)
    @property
    def padding_box_top(self): return self.top + self.border.get("top", 0)
    @property
    def padding_box_width(self):
        return self.width - self.border.get("left", 0) - self.border.get("right", 0)
    @property
    def padding_box_height(self):
        return self.height - self.border.get("top", 0) - self.border.get("bottom", 0)


#: The four sides a shorthand covers, clockwise from the top.
_SIDES = ("top", "right", "bottom", "left")


#: What an inline-level box takes part in without leaving the line it is on.
_INLINE = ("inline", "inline-block", "inline-flex", "inline-grid", "inline-table", "ruby")


#: The default object size a replaced element falls back to when its content has no
#: intrinsic size of its own, which is what an empty iframe, canvas or video measures.
_DEFAULT_OBJECT_SIZE = {
    "iframe": (300.0, 150.0), "canvas": (300.0, 150.0), "video": (300.0, 150.0),
    "object": (300.0, 150.0), "svg": (300.0, 150.0),
}


def _expand(value):
    """The four lengths a CSS shorthand holds."""
    parts = str(value).split()
    if not parts:
        return ["0"] * 4
    if len(parts) == 1:
        return parts * 4
    if len(parts) == 2:
        return [parts[0], parts[1], parts[0], parts[1]]
    if len(parts) == 3:
        return [parts[0], parts[1], parts[2], parts[1]]
    return parts[:4]


def _sides(style, name, reference, default="0"):
    """Per-side lengths, the shorthand standing in for whatever is not set."""
    values = _expand(style.get(name, default))
    return {side: _px(style.get(f"{name}-{side}", values[index]), reference)
            for index, side in enumerate(_SIDES)}


def _auto(value):
    return value is None or str(value).strip().lower() in ("", "auto")


def _style_of(element, rules):
    return _cascade(element, rules)


@dataclass(frozen=True)
class _Containing:
    """The block a box is measured against, in viewport coordinates."""
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class _Walk:
    """One layout pass: the rules to cascade, where boxes go, and the viewport."""
    rules: tuple
    collected: dict | None
    viewport: tuple


def _viewport_width(element):
    realm = getattr(getattr(element, "_services", None), "realm_services", None)
    viewport = getattr(getattr(realm, "environment", None), "viewport", None)
    width = getattr(viewport, "width", None) or 1280
    height = getattr(viewport, "height", None) or 720
    return float(width), float(height)


def _offset(style, forward, backward, size):
    """A relative offset: the first side set wins, and ``auto`` means none."""
    if not _auto(style.get(forward)):
        return _px(style[forward], size)
    if not _auto(style.get(backward)):
        return -_px(style[backward], size)
    return 0.0


def _place(element, style, walk, containing, positioned):
    """Lay *element* out inside *containing* and answer its border box.

    *positioned* is the padding box of the nearest positioned ancestor, which is what
    an absolutely positioned box measures against; ``None`` means the viewport, the
    initial containing block of a page.
    """
    def record(box):
        if walk.collected is not None:
            walk.collected[element] = box
        return box

    if style.get("display") == "none" or not getattr(element, "isConnected", False):
        return record(Box())

    padding = _sides(style, "padding", containing.width)
    border = _sides(style, "border-width", containing.width)
    margin = _sides(style, "margin", containing.width)
    horizontal = padding["left"] + padding["right"] + border["left"] + border["right"]
    vertical = padding["top"] + padding["bottom"] + border["top"] + border["bottom"]

    declared_width = style.get("width", "auto")
    available = max(0.0, containing.width - margin["left"] - margin["right"] - horizontal)
    default_size = _DEFAULT_OBJECT_SIZE.get(str(getattr(element, "localName", "")).lower())
    if _auto(declared_width):
        if default_size is not None:
            # A replaced element with no intrinsic size of its own takes the default
            # object size rather than shrinking to a content it does not have.
            content_width = default_size[0]
        else:
            # An inline-level box shrinks around its content and an empty one stays
            # empty; a block-level box fills what is left of its containing block.
            content_width = (min(_intrinsic_width(element, style, walk.rules), available)
                             if style.get("display", "block") in _INLINE else available)
    else:
        width = _px(declared_width, containing.width)
        content_width = max(0.0, width - horizontal
                            if style.get("box-sizing", "content-box") == "border-box"
                            else width)
    border_width = content_width + horizontal

    position = style.get("position", "static")
    left = containing.left + margin["left"]
    top = containing.top + margin["top"]
    declared_height = style.get("height", "auto")
    if not _auto(declared_height):
        content_height = max(0.0, _px(declared_height, containing.height))
    elif default_size is not None:
        content_height = default_size[1]
    else:
        content_height = None
    if position in ("absolute", "fixed"):
        block = positioned or _Containing(0.0, 0.0, *walk.viewport)
        if not _auto(style.get("left")):
            left = block.left + _px(style["left"], block.width) + margin["left"]
        elif not _auto(style.get("right")):
            left = (block.left + block.width - _px(style["right"], block.width)
                    - margin["right"] - border_width)
        if not _auto(style.get("top")):
            top = block.top + _px(style["top"], block.height) + margin["top"]
        elif not _auto(style.get("bottom")) and content_height is not None:
            top = (block.top + block.height - _px(style["bottom"], block.height)
                   - margin["bottom"] - content_height - vertical)

    content_left = left + border["left"] + padding["left"]
    content_top = top + border["top"] + padding["top"]
    inner = _Containing(content_left, content_top, content_width,
                        content_height if content_height is not None else 0.0)
    # A positioned box becomes the containing block for its own absolute descendants,
    # measured on the padding box, which is why the offsets exclude the border.
    child_positioned = positioned
    if position != "static":
        child_positioned = _Containing(
            left + border["left"], top + border["top"],
            max(0.0, border_width - horizontal),
            content_height + vertical if content_height is not None else 0.0)
    used_height = _children(element, style, walk, inner, child_positioned)
    if content_height is None:
        content_height = used_height
    border_height = content_height + vertical

    if position == "relative":
        offset_x = _offset(style, "left", "right", containing.width)
        offset_y = _offset(style, "top", "bottom", containing.height)
        if offset_x or offset_y:
            left += offset_x
            top += offset_y
            _shift(element, walk, offset_x, offset_y)

    return record(Box(width=border_width, height=border_height, left=left, top=top,
                      padding=padding, border=border, margin=margin))


def _shift(element, walk, x, y):
    """Move everything the subtree recorded, for a relative offset applied afterwards."""
    if walk.collected is None or (not x and not y):
        return
    for node, box in list(walk.collected.items()):
        if node is element or _contains(element, node):
            box.left += x
            box.top += y


def _contains(ancestor, node):
    current = getattr(node, "parentElement", None)
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "parentElement", None)
    return False


def _children(element, style, walk, containing, positioned):
    """Lay out *element*'s children and answer the content height they take."""
    display = style.get("display", "block")
    if display in ("flex", "inline-flex"):
        return _flex_children(element, style, walk, containing, positioned)
    if display in ("grid", "inline-grid"):
        return _grid_children(element, style, walk, containing, positioned)
    return _flow_children(element, style, walk, containing, positioned)


def _child_elements(element):
    return [child for child in getattr(element, "_children", ())
            if isinstance(child, (Element, Text))]


def _intrinsic_width(element, style, rules, depth=0):
    """The width a shrink-to-fit box would take: the widest line of its content."""
    if depth > 32:
        return 0.0
    inline = 0.0
    widest = 0.0
    for child in _child_elements(element):
        if isinstance(child, Text):
            inline += text_width(child.get_textContent() or "", style)
            continue
        child_style = _style_of(child, rules)
        if child_style.get("display") == "none":
            continue
        edges = _sides(child_style, "margin", 0.0)
        edges.update(_sides(child_style, "padding", 0.0))
        edges.update(_sides(child_style, "border-width", 0.0))
        extra = sum(edges.values())
        declared = child_style.get("width", "auto")
        width = (_px(declared, 0.0) if not _auto(declared)
                 else _intrinsic_width(child, child_style, rules, depth + 1))
        if child_style.get("display", "block") in _INLINE:
            inline += width + extra
        else:
            inline = 0.0
            widest = max(widest, width + extra)
    return max(inline, widest)


def _line_height(style):
    return (_px(style.get("line-height", "normal"), 0)
            or _px(style.get("font-size", "16px"), 16) * 1.2)


def _flow_children(element, style, walk, containing, positioned):
    """Block boxes stacked and inline-level ones laid along a line."""
    cursor = 0.0
    line_x = 0.0
    line_height = 0.0

    def end_line():
        nonlocal cursor, line_x, line_height
        if line_height:
            cursor += line_height
        line_x = 0.0
        line_height = 0.0

    for child in _child_elements(element):
        if isinstance(child, Text):
            content = child.get_textContent() or ""
            if not content.strip() and not line_x:
                # Collapsible whitespace with nothing before it makes no line box,
                # which is what keeps the newlines between blocks out of the flow.
                continue
            line_x += text_width(content, style)
            line_height = max(line_height, _line_height(style))
            continue
        child_style = _style_of(child, walk.rules)
        if child_style.get("display") == "none":
            continue
        if child_style.get("position", "static") in ("absolute", "fixed"):
            # Out of flow: it keeps where the cursor is as its static position and
            # leaves the cursor alone.
            end_line()
            _place(child, child_style, walk,
                   _Containing(containing.left, containing.top + cursor, containing.width,
                               containing.height),
                   positioned)
            continue
        child_margin = _sides(child_style, "margin", containing.width)
        if child_style.get("display", "block") in _INLINE:
            outer = (_intrinsic_width(child, child_style, walk.rules)
                     + child_margin["left"] + child_margin["right"])
            if line_x and outer and line_x + outer > containing.width:
                end_line()
            placed = _place(child, child_style, walk,
                            _Containing(containing.left + line_x, containing.top + cursor,
                                        containing.width - line_x, containing.height),
                            positioned)
            line_x += placed.width + child_margin["left"] + child_margin["right"]
            line_height = max(line_height, placed.height + child_margin["top"] + child_margin["bottom"])
            continue
        end_line()
        placed = _place(child, child_style, walk,
                        _Containing(containing.left, containing.top + cursor, containing.width,
                                    containing.height),
                        positioned)
        cursor += child_margin["top"] + placed.height + child_margin["bottom"]
    end_line()
    return cursor


def _flex_children(element, style, walk, containing, positioned):
    row = style.get("flex-direction", "row") not in ("column", "column-reverse")
    children = [child for child in _child_elements(element) if isinstance(child, Element)]
    if row:
        cursor = 0.0
        tallest = 0.0
        for child in children:
            child_style = _style_of(child, walk.rules)
            placed = _place(child, child_style, walk,
                            _Containing(containing.left + cursor, containing.top,
                                        max(0.0, containing.width - cursor), containing.height),
                            positioned)
            cursor += placed.width
            tallest = max(tallest, placed.height)
        return tallest
    cursor = 0.0
    for child in children:
        child_style = _style_of(child, walk.rules)
        placed = _place(child, child_style, walk,
                        _Containing(containing.left, containing.top + cursor, containing.width,
                                    max(0.0, containing.height - cursor)),
                        positioned)
        cursor += placed.height
    return cursor


def _grid_children(element, style, walk, containing, positioned):
    columns = max(1, len(str(style.get("grid-template-columns", "1fr")).split()))
    cell = containing.width / columns
    children = [child for child in _child_elements(element) if isinstance(child, Element)]
    cursor = 0.0
    row_height = 0.0
    for index, child in enumerate(children):
        column = index % columns
        if column == 0 and index:
            cursor += row_height
            row_height = 0.0
        child_style = _style_of(child, walk.rules)
        placed = _place(child, child_style, walk,
                        _Containing(containing.left + column * cell, containing.top + cursor,
                                    cell, containing.height),
                        positioned)
        row_height = max(row_height, placed.height)
    return cursor + row_height


def layout(element, containing_width=None, top=0.0, rules=None, style=None, collected=None):
    """*element*'s border box in viewport coordinates, laying the document out.

    The document is walked once per call: a box only knows where it sits after its
    ancestors have placed it, so ``containing_width`` and ``top`` are ignored and
    every recorded box carries its own viewport coordinates.
    """
    document = getattr(element, "ownerDocument", None)
    if document is None:
        return Box()
    boxes = {} if collected is None else collected
    root = document.get_documentElement()
    width, height = _viewport_width(element)
    if isinstance(root, Element):
        walk = _Walk(rules or document_rules(document), boxes, (width, height))
        _place(root, _style_of(root, walk.rules), walk,
               _Containing(0.0, 0.0, width, height), None)
    return boxes.get(element, Box())


def positioned_box(element):
    """*element*'s box with the offset its ancestors put it at."""
    return layout(element)


def offset_parent(element):
    """The box an element's ``offsetLeft`` is measured against, or ``None``."""
    if not getattr(element, "isConnected", False):
        return None
    document = getattr(element, "ownerDocument", None)
    if getattr(element, "localName", "") in ("html", "body"):
        return None
    current = getattr(element, "parentElement", None)
    while current is not None:
        style = computed_style(current)
        if style.get("position", "static") != "static":
            return current
        if getattr(current, "localName", "") in ("body", "td", "th", "table"):
            return current
        current = getattr(current, "parentElement", None)
    if document is not None:
        return document.get_body()
    return None


def computed_style_object(element, *, rendered=True):
    """The cascade as a declaration block: the same interface, and nothing may write it back."""
    return CSSStyleDeclaration(element=element,
                               computed=computed_style(element) if rendered else {})

__all__ = [
    "Box", "computed_style", "computed_style_object", "document_rules", "layout",
    "offset_parent", "positioned_box", "text_width",
]
