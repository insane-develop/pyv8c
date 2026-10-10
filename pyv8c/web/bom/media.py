"""``window.matchMedia`` and the list it hands back.

Only viewport features are resolved; anything else answers from desktop
defaults.  A viewport that never resizes never fires a change.
"""

import re


from ...idl import interface
from ...foundation.arguments import MISSING, missing_error
from ..dom.events import EventTarget


#: ``(min-width: 40em)``: one feature per match.
_FEATURE_RE = re.compile(r"\(\s*([a-z-]+)\s*:\s*([^)]*)\)", re.I)


#: What a desktop browser reports for the features this one does not model.
_DEFAULT_FEATURES = {
    "prefers-color-scheme": "light",
    "prefers-reduced-motion": "no-preference",
    "prefers-contrast": "no-preference",
    "hover": "hover",
    "any-hover": "hover",
    "pointer": "fine",
    "any-pointer": "fine",
    "display-mode": "browser",
    "scripting": "enabled",
    "update": "fast",
    "overflow-block": "scroll",
    "overflow-inline": "scroll",
    "color-gamut": "srgb",
    "forced-colors": "none",
}


def _pixels(text):
    """A media-query length in pixels, or None when it is not a length."""
    match = re.match(r"^\s*(-?(?:\d+\.?\d*|\.\d+))\s*(px|em|rem)?\s*$", str(text))
    if not match:
        return None
    value = float(match.group(1))
    return value * 16.0 if (match.group(2) or "px").lower() in ("em", "rem") else value


def _matches_feature(name, value, width, height):
    name, value = name.lower(), value.strip().lower()
    if name in ("min-width", "max-width", "width"):
        limit = _pixels(value)
        if limit is None:
            return False
        return {"min-width": width >= limit, "max-width": width <= limit}.get(name, width == limit)
    if name in ("min-height", "max-height", "height"):
        limit = _pixels(value)
        if limit is None:
            return False
        return {"min-height": height >= limit, "max-height": height <= limit}.get(name, height == limit)
    if name == "orientation":
        return value == ("landscape" if width >= height else "portrait")
    if name == "aspect-ratio":
        limit = _pixels(value)
        return limit is not None and abs((width / height if height else 0) - limit) < 1e-9
    return _DEFAULT_FEATURES.get(name) == value


def evaluate(query, width, height):
    """Whether *query* holds for a viewport of this size; an unknown feature
    answers false, as a browser answers an unsupported feature.
    """
    for alternative in str(query).split(","):
        alternative = alternative.strip().lower()
        if not alternative:
            continue
        negated = alternative.startswith("not ")
        if negated:
            alternative = alternative[4:].strip()
        head = alternative.split(" and ", 1)[0].strip()
        matches = head in ("", "all", "screen") if not head.startswith("(") else True
        matches = matches and all(
            _matches_feature(name, value, width, height)
            for name, value in _FEATURE_RE.findall(alternative)
        )
        if matches != negated:
            return True
    return False


@interface('MediaQueryList', 'EventTarget')
class MediaQueryList(EventTarget):
    """``matchMedia``'s answer: the query and whether it holds right now."""

    def __init__(self, query="", width=0, height=0, **_):
        self._query = str(query)
        self._matches = evaluate(self._query, width, height)

    def get_media(self): return self._query
    def get_matches(self): return bool(self._matches)
    def get_onchange(self): return self._get_handler("onchange")
    def set_onchange(self, value): self._set_handler("onchange", value)

    def fn_addListener(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error('MediaQueryList', 'addListener', 1, 0, constructor=False)
        return self.fn_addEventListener("change", callback)

    def fn_removeListener(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error('MediaQueryList', 'removeListener', 1, 0, constructor=False)
        return self.fn_removeEventListener("change", callback)

__all__ = ["MediaQueryList", "evaluate"]
