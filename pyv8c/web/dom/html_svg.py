"""The SVG geometry interfaces: a rect and the animated lengths its numbers are read
through.

A presentation attribute is the whole truth: a rect's ``width`` is an
``SVGAnimatedLength`` over the attribute of that name, and the ``SVGLength`` behind it
reports the number as written, its unit, and -- for absolute units -- user units.
"""

import re


from ...idl import interface
from ...foundation.arguments import dom_string, number
from ..page_errors import NotSupportedError
from .css import CSSStyleDeclaration
from .css_typed import StylePropertyMap
from .html_elements import Element

__all__ = ["SVGAnimatedLength", "SVGElement", "SVGGeometryElement", "SVGGraphicsElement",
           "SVGLength", "SVGRectElement"]


#: The unit types an SVGLength reports, named as the baseline names its constants.
_UNKNOWN, _NUMBER, _PERCENTAGE, _EMS, _EXS, _PX, _CM, _MM, _IN, _PT, _PC = range(11)


#: A length as written: a number, an optional unit, nothing else.
_LENGTH = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z%]*)$",
                     re.IGNORECASE)


#: What an absolute unit is worth in user units, with the type it reports.
_ABSOLUTE = {"": (_NUMBER, 1.0), "px": (_PX, 1.0), "in": (_IN, 96.0),
             "cm": (_CM, 96.0 / 2.54), "mm": (_MM, 96.0 / 25.4),
             "pt": (_PT, 96.0 / 72.0), "pc": (_PC, 16.0)}
#: The units that mean nothing until something is rendered against them.
_RELATIVE = {"%": _PERCENTAGE, "em": _EMS, "ex": _EXS}


def parse_length(text):
    """``(amount, unitType, specified)`` for a presentation attribute; a missing or
    unparseable one is zero, which is what Chrome reports for those.
    """
    raw = "" if text is None else str(text).strip()
    match = _LENGTH.match(raw)
    if match is None:
        return 0.0, _NUMBER, "0"
    amount, unit = float(match.group(1)), match.group(2).lower()
    if unit in _ABSOLUTE:
        return amount, _ABSOLUTE[unit][0], raw
    if unit in _RELATIVE:
        return amount, _RELATIVE[unit], raw
    return 0.0, _NUMBER, "0"


def user_units(amount, unit_type):
    """The same length in user units, the way ``SVGLength.value`` reports it."""
    entry = next((known for known in _ABSOLUTE.values() if known[0] == unit_type), None)
    if entry is None:
        raise NotSupportedError(
            "Failed to read the 'value' property from 'SVGLength': Could not resolve"
            " relative length.")
    return amount * entry[1]


def _suffix(unit_type):
    """The unit an attribute is written with once a value has been set on it."""
    for unit, (known, _) in _ABSOLUTE.items():
        if known == unit_type:
            return unit
    return ""


@interface('SVGElement', 'Element')
class SVGElement(Element):
    """An SVG element carries an inline style like an HTML one does."""

    def get_style(self):
        """The same inline style object an HTML element hands out."""
        value = getattr(self, "_style_declaration", None)
        if value is None:
            value = self._style_declaration = CSSStyleDeclaration(element=self)
        return value

    def get_attributeStyleMap(self):
        value = getattr(self, "_attribute_style_map", None)
        if value is None:
            value = self._attribute_style_map = StylePropertyMap(element=self)
        return value


@interface('SVGGraphicsElement', 'SVGElement')
class SVGGraphicsElement(SVGElement):
    pass


@interface('SVGGeometryElement', 'SVGGraphicsElement')
class SVGGeometryElement(SVGGraphicsElement):
    pass


@interface('SVGLength', None)
class SVGLength:
    """One length of an SVG attribute, as written and in user units."""

    SVG_LENGTHTYPE_UNKNOWN = _UNKNOWN
    SVG_LENGTHTYPE_NUMBER = _NUMBER
    SVG_LENGTHTYPE_PERCENTAGE = _PERCENTAGE
    SVG_LENGTHTYPE_EMS = _EMS
    SVG_LENGTHTYPE_EXS = _EXS
    SVG_LENGTHTYPE_PX = _PX
    SVG_LENGTHTYPE_CM = _CM
    SVG_LENGTHTYPE_MM = _MM
    SVG_LENGTHTYPE_IN = _IN
    SVG_LENGTHTYPE_PT = _PT
    SVG_LENGTHTYPE_PC = _PC

    def __init__(self, *_extra, element=None, name="", **_):
        self._element, self._name = element, str(name)

    def _specified(self):
        text = self._element.attributes.get(self._name) if self._element is not None else None
        return parse_length(text)

    def _write(self, text):
        if self._element is not None:
            self._element.fn_setAttribute(self._name, text)

    def get_unitType(self): return self._specified()[1]
    def get_value(self): return user_units(*self._specified()[:2])
    def get_valueInSpecifiedUnits(self): return self._specified()[0]
    def get_valueAsString(self): return self._specified()[2]
    def set_value(self, value):
        """``value`` is in user units; the attribute keeps the unit it had."""
        self._write("%g%s" % (float(number(value)), _suffix(self._specified()[1])))
    def set_valueInSpecifiedUnits(self, value):
        """``valueInSpecifiedUnits`` is in the attribute's own unit."""
        self._write("%g%s" % (float(number(value)), _suffix(self._specified()[1])))
    def set_valueAsString(self, value): self._write(dom_string(value))


@interface('SVGAnimatedLength', None)
class SVGAnimatedLength:
    """The animated value behind a geometry attribute.

    Nothing animates here, so both name the attribute -- and they are still two objects.
    """

    def __init__(self, *_extra, element=None, name="", **_):
        self._element, self._name = element, str(name)
        self._lengths = {}

    def get_baseVal(self): return self._length("base")
    def get_animVal(self): return self._length("anim")

    def _length(self, slot):
        length = self._lengths.get(slot)
        if length is None:
            length = self._lengths[slot] = SVGLength(element=self._element, name=self._name)
        return length


@interface('SVGRectElement', 'SVGGeometryElement')
class SVGRectElement(SVGGeometryElement):
    """The rectangle, whose six numbers are all presentation attributes."""

    # The six numbers are ``get_<name>`` over baseline members, so the convention binds them.

    def _animated(self, name):
        """The animated value for one of the six, held so a second read is the same
        object, which is what Chrome hands back.
        """
        lengths = getattr(self, "_lengths", None)
        if lengths is None:
            lengths = self._lengths = {}
        animated = lengths.get(name)
        if animated is None:
            animated = lengths[name] = SVGAnimatedLength(element=self, name=name)
        return animated

    def get_x(self): return self._animated("x")
    def get_y(self): return self._animated("y")
    def get_width(self): return self._animated("width")
    def get_height(self): return self._animated("height")
    def get_rx(self): return self._animated("rx")
    def get_ry(self): return self._animated("ry")
