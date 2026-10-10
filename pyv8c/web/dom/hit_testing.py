"""Which element a point lands on, and the stack of elements behind it.

The boxes ``getBoundingClientRect`` reports are the ones hit-tested, so a point
outside the viewport reports ``null`` where Chrome's ``elementFromPoint`` does.
``visibility:hidden``/``pointer-events:none`` skip the element itself but keep
its descendants reachable; the topmost element wins and the rest of the stack is
deepest first.  Measured against Chrome 153.
"""

import pyv8

from .html_elements import Element
from .layout import Box, computed_style, layout, positioned_box


#: Viewport used when a document has no environment to ask, matching ``dom.layout``.
_DEFAULT_VIEWPORT = (1280, 720)

_COLLAPSED_VISIBILITY = ("hidden", "collapse")


def border_box(element):
    """The element's box in viewport coordinates, as ``getBoundingClientRect`` reports it."""
    return positioned_box(element)


def _inside(box, x, y):
    """Whether the point is in the box, half-open as Chromium tests it."""
    return (box.left <= x < box.left + box.width
            and box.top <= y < box.top + box.height)


def contains(element, x, y):
    """Whether the point is inside *element*'s box, positioned as the hit test reads it."""
    return _inside(border_box(element), x, y)


def viewport_size(document):
    """``(width, height)`` of the document's viewport."""
    services = getattr(document, "_services", None)
    realm = getattr(services, "realm_services", None)
    viewport = getattr(getattr(realm, "environment", None), "viewport", None)
    return (
        getattr(viewport, "width", None) or _DEFAULT_VIEWPORT[0],
        getattr(viewport, "height", None) or _DEFAULT_VIEWPORT[1],
    )


def _hit_testable(style):
    """Whether a box is a hit-test target at all, before its children are tried."""
    return (
        style.get("visibility", "visible") not in _COLLAPSED_VISIBILITY
        and style.get("pointer-events", "auto") != "none"
    )


def _collect(element, boxes, x, y, found):
    """Append the elements under the point in *element*'s subtree, deepest first.
    Children are walked last-to-first so a later sibling stacks above an earlier."""
    box = boxes.get(element)
    if box is None:
        return
    hit = _hit_testable(computed_style(element)) and _inside(box, x, y)
    for child in reversed(list(getattr(element, "_children", ()))):
        if isinstance(child, Element) and computed_style(child).get("display") != "none":
            _collect(child, boxes, x, y, found)
    if hit:
        found.append(element)


def elements_from_point(document, x, y):
    """The elements under a point, topmost first, as ``elementsFromPoint`` reports them."""
    width, height = viewport_size(document)
    if not (0 <= x < width and 0 <= y < height):
        return []
    root = document.get_documentElement()
    if root is None or root is pyv8.Null:
        return []
    boxes = {}
    layout(root, collected=boxes)
    found = []
    _collect(root, boxes, x, y, found)
    if not any(node is root for node in found):
        # Chromium reports the root for every point in the viewport, whatever
        # its content box measured.
        found.append(root)
    return found


def element_from_point(document, x, y):
    """The topmost element under a point, or ``None`` when the point is outside."""
    stack = elements_from_point(document, x, y)
    return stack[0] if stack else None

__all__ = [
    "Box", "border_box", "contains", "element_from_point", "elements_from_point",
    "viewport_size",
]
