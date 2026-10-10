"""CSSOM: inline ``HTMLElement.style`` declarations and the table of property
names a ``CSSStyleDeclaration`` resolves as named properties."""
import re
from urllib.parse import urljoin

import pyv8
from pyv8.flag import ArrayProto, V8Attribute
from ...idl import interface
from ...foundation.arguments import MISSING, boolean, dom_string, integer, missing_error
from ..page_errors import IndexSizeError, PageTypeError
from .mixins import LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin
from .utils import OBJECT_PROTOTYPE_NAMES, camel_name as _camel_name, dash_name as _dash_name

_NAME_RE = re.compile(r"^(?:--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*)$")
_OBJECT_NAMES = OBJECT_PROTOTYPE_NAMES | {
    "cssText", "length", "parentRule", "cssFloat", "getPropertyPriority",
    "getPropertyValue", "item", "removeProperty", "setProperty",
}


def _property_name(value):
    value = str(dom_string(value)).strip()
    if value.startswith("--"):
        return value if _NAME_RE.match(value) else ""
    value = value.lower()
    return value if _NAME_RE.match(value) else ""


def _normalize_value(value):
    value = str(dom_string(value)).strip()
    if not value or any(ch in value for ch in "{};\x00"): return ""
    return re.sub(r"[ \t\r\n]+", " ", value)


def _split_priority(value):
    match = re.search(r"\s*!\s*important\s*$", value, re.I)
    return (value[:match.start()].rstrip(), "important") if match else (value, "")


# ``'color' in style`` only answers for properties the engine supports, so the
# list matches what Chrome reports: ``-webkit-`` forms because Blink ships them,
# no ``-moz-``/``-ms-``/``-o-``, and custom properties always accepted.


#: camelCase, as reported by a real Chrome CSSStyleDeclaration, in the order it
#: enumerates them: a declaration moves no name and a custom property is not listed.
_CAMEL_NAMES = (
    "accentColor", "additiveSymbols", "alignContent", "alignItems", "alignSelf", "alignmentBaseline", "all",
    "anchorName", "anchorScope", "animation", "animationComposition", "animationDelay", "animationDirection",
    "animationDuration", "animationFillMode", "animationIterationCount", "animationName", "animationPlayState",
    "animationRange", "animationRangeEnd", "animationRangeStart", "animationTimeline",
    "animationTimingFunction", "animationTrigger", "appRegion", "appearance", "ascentOverride", "aspectRatio",
    "backdropFilter", "backfaceVisibility", "background", "backgroundAttachment", "backgroundBlendMode",
    "backgroundClip", "backgroundColor", "backgroundImage", "backgroundOrigin", "backgroundPosition",
    "backgroundPositionX", "backgroundPositionY", "backgroundRepeat", "backgroundSize", "basePalette",
    "baselineShift", "baselineSource", "blockSize", "border", "borderBlock", "borderBlockColor",
    "borderBlockEnd", "borderBlockEndColor", "borderBlockEndStyle", "borderBlockEndWidth", "borderBlockStart",
    "borderBlockStartColor", "borderBlockStartStyle", "borderBlockStartWidth", "borderBlockStyle",
    "borderBlockWidth", "borderBottom", "borderBottomColor", "borderBottomLeftRadius",
    "borderBottomRightRadius", "borderBottomStyle", "borderBottomWidth", "borderCollapse", "borderColor",
    "borderEndEndRadius", "borderEndStartRadius", "borderImage", "borderImageOutset", "borderImageRepeat",
    "borderImageSlice", "borderImageSource", "borderImageWidth", "borderInline", "borderInlineColor",
    "borderInlineEnd", "borderInlineEndColor", "borderInlineEndStyle", "borderInlineEndWidth",
    "borderInlineStart", "borderInlineStartColor", "borderInlineStartStyle", "borderInlineStartWidth",
    "borderInlineStyle", "borderInlineWidth", "borderLeft", "borderLeftColor", "borderLeftStyle",
    "borderLeftWidth", "borderRadius", "borderRight", "borderRightColor", "borderRightStyle",
    "borderRightWidth", "borderShape", "borderSpacing", "borderStartEndRadius", "borderStartStartRadius",
    "borderStyle", "borderTop", "borderTopColor", "borderTopLeftRadius", "borderTopRightRadius",
    "borderTopStyle", "borderTopWidth", "borderWidth", "bottom", "boxDecorationBreak", "boxShadow",
    "boxSizing", "breakAfter", "breakBefore", "breakInside", "bufferedRendering", "captionSide",
    "caretAnimation", "caretColor", "caretShape", "clear", "clip", "clipPath", "clipRule", "color",
    "colorInterpolation", "colorInterpolationFilters", "colorRendering", "colorScheme", "columnCount",
    "columnFill", "columnGap", "columnHeight", "columnRule", "columnRuleBreak", "columnRuleColor",
    "columnRuleInset", "columnRuleInsetCap", "columnRuleInsetCapEnd", "columnRuleInsetCapStart",
    "columnRuleInsetEnd", "columnRuleInsetJunction", "columnRuleInsetJunctionEnd",
    "columnRuleInsetJunctionStart", "columnRuleInsetStart", "columnRuleStyle", "columnRuleVisibilityItems",
    "columnRuleWidth", "columnSpan", "columnWidth", "columnWrap", "columns", "contain",
    "containIntrinsicBlockSize", "containIntrinsicHeight", "containIntrinsicInlineSize",
    "containIntrinsicSize", "containIntrinsicWidth", "container", "containerName", "containerType", "content",
    "contentVisibility", "cornerBlockEndShape", "cornerBlockStartShape", "cornerBottomLeftShape",
    "cornerBottomRightShape", "cornerBottomShape", "cornerEndEndShape", "cornerEndStartShape",
    "cornerInlineEndShape", "cornerInlineStartShape", "cornerLeftShape", "cornerRightShape", "cornerShape",
    "cornerStartEndShape", "cornerStartStartShape", "cornerTopLeftShape", "cornerTopRightShape",
    "cornerTopShape", "counterIncrement", "counterReset", "counterSet", "cursor", "cx", "cy", "d",
    "descentOverride", "direction", "display", "dominantBaseline", "dynamicRangeLimit", "emptyCells",
    "fallback", "fieldSizing", "fill", "fillOpacity", "fillRule", "filter", "flex", "flexBasis",
    "flexDirection", "flexFlow", "flexGrow", "flexLineCount", "flexShrink", "flexWrap", "float", "floodColor",
    "floodOpacity", "font", "fontDisplay", "fontFamily", "fontFeatureSettings", "fontKerning",
    "fontLanguageOverride", "fontOpticalSizing", "fontPalette", "fontSize", "fontSizeAdjust", "fontStretch",
    "fontStyle", "fontSynthesis", "fontSynthesisSmallCaps", "fontSynthesisStyle", "fontSynthesisWeight",
    "fontVariant", "fontVariantAlternates", "fontVariantCaps", "fontVariantEastAsian", "fontVariantEmoji",
    "fontVariantLigatures", "fontVariantNumeric", "fontVariantPosition", "fontVariationSettings", "fontWeight",
    "forcedColorAdjust", "frameSizing", "gap", "grid", "gridArea", "gridAutoColumns", "gridAutoFlow",
    "gridAutoRows", "gridColumn", "gridColumnEnd", "gridColumnGap", "gridColumnStart", "gridGap", "gridRow",
    "gridRowEnd", "gridRowGap", "gridRowStart", "gridTemplate", "gridTemplateAreas", "gridTemplateColumns",
    "gridTemplateRows", "height", "hyphenateCharacter", "hyphenateLimitChars", "hyphens", "imageOrientation",
    "imageRendering", "inherits", "initialLetter", "initialValue", "inlineSize", "inset", "insetBlock",
    "insetBlockEnd", "insetBlockStart", "insetInline", "insetInlineEnd", "insetInlineStart", "interactivity",
    "interestDelay", "interestDelayEnd", "interestDelayStart", "interpolateSize", "isolation",
    "justifyContent", "justifyItems", "justifySelf", "left", "letterSpacing", "lightingColor", "lineBreak",
    "lineGapOverride", "lineHeight", "listStyle", "listStyleImage", "listStylePosition", "listStyleType",
    "margin", "marginBlock", "marginBlockEnd", "marginBlockStart", "marginBottom", "marginInline",
    "marginInlineEnd", "marginInlineStart", "marginLeft", "marginRight", "marginTop", "marker", "markerEnd",
    "markerMid", "markerStart", "mask", "maskClip", "maskComposite", "maskImage", "maskMode", "maskOrigin",
    "maskPosition", "maskRepeat", "maskSize", "maskType", "mathDepth", "mathShift", "mathStyle",
    "maxBlockSize", "maxHeight", "maxInlineSize", "maxWidth", "minBlockSize", "minHeight", "minInlineSize",
    "minWidth", "mixBlendMode", "navigation", "negative", "objectFit", "objectPosition", "objectViewBox",
    "offset", "offsetAnchor", "offsetDistance", "offsetPath", "offsetPosition", "offsetRotate", "opacity",
    "order", "orphans", "outline", "outlineColor", "outlineOffset", "outlineStyle", "outlineWidth", "overflow",
    "overflowAnchor", "overflowBlock", "overflowClipMargin", "overflowInline", "overflowWrap", "overflowX",
    "overflowY", "overlay", "overrideColors", "overscrollBehavior", "overscrollBehaviorBlock",
    "overscrollBehaviorInline", "overscrollBehaviorX", "overscrollBehaviorY", "pad", "padding", "paddingBlock",
    "paddingBlockEnd", "paddingBlockStart", "paddingBottom", "paddingInline", "paddingInlineEnd",
    "paddingInlineStart", "paddingLeft", "paddingRight", "paddingTop", "page", "pageBreakAfter",
    "pageBreakBefore", "pageBreakInside", "pageMarginSafety", "pageOrientation", "paintOrder", "perspective",
    "perspectiveOrigin", "placeContent", "placeItems", "placeSelf", "pointerEvents", "position",
    "positionAnchor", "positionArea", "positionTry", "positionTryFallbacks", "positionTryOrder",
    "positionVisibility", "prefix", "printColorAdjust", "quotes", "r", "range", "readingFlow", "readingOrder",
    "resize", "result", "right", "rotate", "rowGap", "rowRule", "rowRuleBreak", "rowRuleColor", "rowRuleInset",
    "rowRuleInsetCap", "rowRuleInsetCapEnd", "rowRuleInsetCapStart", "rowRuleInsetEnd", "rowRuleInsetJunction",
    "rowRuleInsetJunctionEnd", "rowRuleInsetJunctionStart", "rowRuleInsetStart", "rowRuleStyle",
    "rowRuleVisibilityItems", "rowRuleWidth", "rubyAlign", "rubyOverhang", "rubyPosition", "rule", "ruleBreak",
    "ruleColor", "ruleInset", "ruleInsetCap", "ruleInsetEnd", "ruleInsetJunction", "ruleInsetStart",
    "ruleOverlap", "ruleStyle", "ruleVisibilityItems", "ruleWidth", "rx", "ry", "scale", "scrollAxisLock",
    "scrollBehavior", "scrollInitialTarget", "scrollMargin", "scrollMarginBlock", "scrollMarginBlockEnd",
    "scrollMarginBlockStart", "scrollMarginBottom", "scrollMarginInline", "scrollMarginInlineEnd",
    "scrollMarginInlineStart", "scrollMarginLeft", "scrollMarginRight", "scrollMarginTop", "scrollMarkerGroup",
    "scrollPadding", "scrollPaddingBlock", "scrollPaddingBlockEnd", "scrollPaddingBlockStart",
    "scrollPaddingBottom", "scrollPaddingInline", "scrollPaddingInlineEnd", "scrollPaddingInlineStart",
    "scrollPaddingLeft", "scrollPaddingRight", "scrollPaddingTop", "scrollSnapAlign", "scrollSnapStop",
    "scrollSnapType", "scrollTargetGroup", "scrollTimeline", "scrollTimelineAxis", "scrollTimelineName",
    "scrollbarColor", "scrollbarGutter", "scrollbarWidth", "shapeImageThreshold", "shapeMargin",
    "shapeOutside", "shapeRendering", "size", "sizeAdjust", "speak", "speakAs", "src", "stopColor",
    "stopOpacity", "stroke", "strokeDasharray", "strokeDashoffset", "strokeLinecap", "strokeLinejoin",
    "strokeMiterlimit", "strokeOpacity", "strokeWidth", "suffix", "symbols", "syntax", "system", "tabSize",
    "tableLayout", "textAlign", "textAlignLast", "textAnchor", "textAutospace", "textBox", "textBoxEdge",
    "textBoxTrim", "textCombineUpright", "textDecoration", "textDecorationColor", "textDecorationLine",
    "textDecorationSkipInk", "textDecorationStyle", "textDecorationThickness", "textEmphasis",
    "textEmphasisColor", "textEmphasisPosition", "textEmphasisStyle", "textFit", "textIndent", "textJustify",
    "textOrientation", "textOverflow", "textRendering", "textShadow", "textSizeAdjust", "textSpacingTrim",
    "textTransform", "textUnderlineOffset", "textUnderlinePosition", "textWrap", "textWrapMode",
    "textWrapStyle", "timelineScope", "timelineTrigger", "timelineTriggerActivationRange",
    "timelineTriggerActivationRangeEnd", "timelineTriggerActivationRangeStart", "timelineTriggerActiveRange",
    "timelineTriggerActiveRangeEnd", "timelineTriggerActiveRangeStart", "timelineTriggerName",
    "timelineTriggerSource", "top", "touchAction", "transform", "transformBox", "transformOrigin",
    "transformStyle", "transition", "transitionBehavior", "transitionDelay", "transitionDuration",
    "transitionProperty", "transitionTimingFunction", "translate", "triggerScope", "types", "unicodeBidi",
    "unicodeRange", "userSelect", "vectorEffect", "verticalAlign", "viewTimeline", "viewTimelineAxis",
    "viewTimelineInset", "viewTimelineName", "viewTransitionClass", "viewTransitionGroup",
    "viewTransitionName", "viewTransitionScope", "visibility", "webkitAlignContent", "webkitAlignItems",
    "webkitAlignSelf", "webkitAnimation", "webkitAnimationDelay", "webkitAnimationDirection",
    "webkitAnimationDuration", "webkitAnimationFillMode", "webkitAnimationIterationCount",
    "webkitAnimationName", "webkitAnimationPlayState", "webkitAnimationTimingFunction", "webkitAppRegion",
    "webkitAppearance", "webkitBackfaceVisibility", "webkitBackgroundClip", "webkitBackgroundOrigin",
    "webkitBackgroundSize", "webkitBorderAfter", "webkitBorderAfterColor", "webkitBorderAfterStyle",
    "webkitBorderAfterWidth", "webkitBorderBefore", "webkitBorderBeforeColor", "webkitBorderBeforeStyle",
    "webkitBorderBeforeWidth", "webkitBorderBottomLeftRadius", "webkitBorderBottomRightRadius",
    "webkitBorderEnd", "webkitBorderEndColor", "webkitBorderEndStyle", "webkitBorderEndWidth",
    "webkitBorderHorizontalSpacing", "webkitBorderImage", "webkitBorderRadius", "webkitBorderStart",
    "webkitBorderStartColor", "webkitBorderStartStyle", "webkitBorderStartWidth", "webkitBorderTopLeftRadius",
    "webkitBorderTopRightRadius", "webkitBorderVerticalSpacing", "webkitBoxAlign", "webkitBoxDecorationBreak",
    "webkitBoxDirection", "webkitBoxFlex", "webkitBoxOrdinalGroup", "webkitBoxOrient", "webkitBoxPack",
    "webkitBoxReflect", "webkitBoxShadow", "webkitBoxSizing", "webkitClipPath", "webkitColumnBreakAfter",
    "webkitColumnBreakBefore", "webkitColumnBreakInside", "webkitColumnCount", "webkitColumnGap",
    "webkitColumnRule", "webkitColumnRuleColor", "webkitColumnRuleStyle", "webkitColumnRuleWidth",
    "webkitColumnSpan", "webkitColumnWidth", "webkitColumns", "webkitFilter", "webkitFlex", "webkitFlexBasis",
    "webkitFlexDirection", "webkitFlexFlow", "webkitFlexGrow", "webkitFlexShrink", "webkitFlexWrap",
    "webkitFontFeatureSettings", "webkitFontSmoothing", "webkitHyphenateCharacter", "webkitJustifyContent",
    "webkitLineBreak", "webkitLineClamp", "webkitLocale", "webkitLogicalHeight", "webkitLogicalWidth",
    "webkitMarginAfter", "webkitMarginBefore", "webkitMarginEnd", "webkitMarginStart", "webkitMask",
    "webkitMaskBoxImage", "webkitMaskBoxImageOutset", "webkitMaskBoxImageRepeat", "webkitMaskBoxImageSlice",
    "webkitMaskBoxImageSource", "webkitMaskBoxImageWidth", "webkitMaskClip", "webkitMaskComposite",
    "webkitMaskImage", "webkitMaskOrigin", "webkitMaskPosition", "webkitMaskPositionX", "webkitMaskPositionY",
    "webkitMaskRepeat", "webkitMaskSize", "webkitMaxLogicalHeight", "webkitMaxLogicalWidth",
    "webkitMinLogicalHeight", "webkitMinLogicalWidth", "webkitOpacity", "webkitOrder", "webkitPaddingAfter",
    "webkitPaddingBefore", "webkitPaddingEnd", "webkitPaddingStart", "webkitPerspective",
    "webkitPerspectiveOrigin", "webkitPerspectiveOriginX", "webkitPerspectiveOriginY",
    "webkitPrintColorAdjust", "webkitRtlOrdering", "webkitRubyPosition", "webkitShapeImageThreshold",
    "webkitShapeMargin", "webkitShapeOutside", "webkitTapHighlightColor", "webkitTextCombine",
    "webkitTextDecorationsInEffect", "webkitTextEmphasis", "webkitTextEmphasisColor",
    "webkitTextEmphasisPosition", "webkitTextEmphasisStyle", "webkitTextFillColor", "webkitTextOrientation",
    "webkitTextSecurity", "webkitTextSizeAdjust", "webkitTextStroke", "webkitTextStrokeColor",
    "webkitTextStrokeWidth", "webkitTransform", "webkitTransformOrigin", "webkitTransformOriginX",
    "webkitTransformOriginY", "webkitTransformOriginZ", "webkitTransformStyle", "webkitTransition",
    "webkitTransitionDelay", "webkitTransitionDuration", "webkitTransitionProperty",
    "webkitTransitionTimingFunction", "webkitUserDrag", "webkitUserModify", "webkitUserSelect",
    "webkitWritingMode", "whiteSpace", "whiteSpaceCollapse", "widows", "width", "willChange", "windowDrag",
    "wordBreak", "wordSpacing", "wordWrap", "writingMode", "x", "y", "zIndex", "zoom", "cssText", "length",
    "parentRule", "cssFloat", "getPropertyPriority", "getPropertyValue", "item", "removeProperty",
    "setProperty"
)


def _canonical(name):
    """The dashed, lower-case form every spelling of a property maps onto."""
    return _dash_name(name).lstrip("-").lower()

SUPPORTED_NAMES = frozenset(_canonical(name) for name in _CAMEL_NAMES)


def supported(name):
    """Whether a camel-case, dashed or prefixed spelling names a real property."""
    if name.startswith("--"):
        return True
    return _canonical(name) in SUPPORTED_NAMES


#: Properties whose computed value is inherited from the parent element.
INHERITED_PROPERTIES = frozenset((
    "color", "font-family", "font-size", "font-style", "font-weight",
    "line-height", "letter-spacing", "word-spacing", "visibility", "white-space",
))


#: The initial value the cascade starts from for each supported property.
INITIAL_VALUES = {
    "display": "block", "position": "static", "visibility": "visible",
    "font-family": "sans-serif", "font-size": "16px", "font-weight": "400",
    "font-style": "normal", "line-height": "normal", "letter-spacing": "normal",
    "word-spacing": "normal", "box-sizing": "content-box",
    "color": "rgb(0, 0, 0)", "white-space": "normal",
}


#: The display a tag starts from, measured from Chrome 153's user-agent sheet.
TAG_DISPLAY = {
    "a": "inline", "address": "block", "article": "block", "aside": "block",
    "blockquote": "block", "body": "block", "button": "inline-block", "canvas": "inline",
    "dd": "block", "div": "block", "dl": "block", "dt": "block", "fieldset": "block",
    "figcaption": "block", "figure": "block", "footer": "block", "form": "block",
    "h1": "block", "h2": "block", "h3": "block", "h4": "block", "h5": "block",
    "h6": "block", "head": "none", "header": "block", "hgroup": "block", "hr": "block",
    "html": "block", "iframe": "inline", "img": "inline", "input": "inline-block",
    "legend": "block", "li": "list-item", "link": "none", "main": "block",
    "menu": "block", "meta": "none", "nav": "block", "ol": "block", "option": "block",
    "p": "block", "pre": "block", "script": "none", "section": "block",
    "select": "inline-block", "span": "inline", "style": "none", "summary": "list-item",
    "table": "table", "td": "table-cell", "template": "none", "textarea": "inline-block",
    "tr": "table-row", "ul": "block",
}


#: What an element with no entry in :data:`TAG_DISPLAY` starts from.
UNTAGGED_DISPLAY = "inline"


#: Box defaults the user-agent sheet gives a tag, measured from Chrome 154 in px at
#: the 16px root font size it starts from.
TAG_BOX_INITIALS = {
    "body": {"margin": "8px"},
    "iframe": {"border-width": "2px", "border-style": "inset"},
    "p": {"margin": "16px 0"},
    "blockquote": {"margin": "16px 40px"},
    "figure": {"margin": "16px 40px"},
    "dl": {"margin": "16px 0"},
    "ul": {"margin": "16px 0", "padding-left": "40px"},
    "ol": {"margin": "16px 0", "padding-left": "40px"},
    "dd": {"margin-left": "40px"},
    "pre": {"margin": "13px 0"},
    "hr": {"margin": "8px 0"},
    "h1": {"margin": "21.44px 0"},
    "h2": {"margin": "19.92px 0"},
    "h3": {"margin": "18.72px 0"},
    "h4": {"margin": "21.28px 0"},
    "h5": {"margin": "22.27px 0"},
    "h6": {"margin": "23.17px 0"},
}


def initial_values_for(element):
    """The cascade's starting values for ``element``; ``display`` depends on the tag."""
    tag = getattr(element, "localName", "")
    values = dict(INITIAL_VALUES)
    values["display"] = TAG_DISPLAY.get(tag, UNTAGGED_DISPLAY)
    values.update(TAG_BOX_INITIALS.get(tag, {}))
    return values


def parse_declarations(raw):
    """Parse ``a: b; c: d !important`` into a declaration list.

    A repeated property keeps the position of its first occurrence and the value
    of its last; this is the single parser for ``cssText`` and the cascade reader.
    """
    declarations, positions = [], {}
    for chunk in str(raw).split(";"):
        if ":" not in chunk:
            continue
        name, value = chunk.split(":", 1)
        name = _property_name(name)
        if not name:
            continue
        value, priority = _split_priority(_normalize_value(value))
        if not value:
            continue
        item = (name, value, priority)
        if name in positions:
            declarations[positions[name]] = item
        else:
            positions[name] = len(declarations)
            declarations.append(item)
    return declarations


@interface("CSSStyleDeclaration", None)
class CSSStyleDeclaration:
    #: ``ArrayProto.iterator`` rather than the document's ``iterable``: the four named methods are
    #: absent, and this declaration is what says so.
    __idl_overrides__ = {
        "interface": {
            "array": ArrayProto.iterator,
            "index_del": "__v8_index_del__",
            "index_enum": "__v8_index_enum__",
            "index_get": "__v8_index_get__",
            "index_query": "__v8_index_query__",
            "index_set": "__v8_index_set__",
            "length": 0,
            "name_del": "__v8_name_del__",
            "name_enum": "__v8_name_enum__",
            "name_get": "__v8_name_get__",
            "name_query": "__v8_name_query__",
            "name_set": "__v8_name_set__"
        }
    }
    def __init__(self, element=None, block=None, parent_rule=None, computed=None, **_):
        self._element = element
        self._parent_rule = parent_rule
        #: A rule's style is a declaration block rather than an element's attribute, so
        #: the two read from different places and only one of them is ever set.
        self._block = None if block is None else parse_declarations(str(block))
        #: A computed style reads the cascade instead, and nothing writes it back.
        self._computed_values = None if computed is None else dict(computed)
    def _declarations(self):
        if self._block is not None:
            return list(self._block)
        if self._computed_values is not None:
            return [(name, value, "") for name, value in self._computed_values.items()
                    if value not in ("", None)]
        raw = str(self._element.attributes.get("style", "")) if self._element is not None else ""
        return parse_declarations(raw)
    def _write(self, declarations):
        if self._computed_values is not None:
            return
        if self._block is not None:
            self._block = list(declarations)
            return
        if self._element is None: return
        text = "".join(f"{n}: {v}{' !important' if p else ''}; " for n, v, p in declarations).strip()
        # Chromium leaves the attribute behind and empties it, so removing the last
        # declaration reads back as "" rather than null.
        if text: self._element.attributes["style"] = text
        elif "style" in self._element.attributes: self._element.attributes["style"] = ""
    def _set(self, name, value, priority=""):
        name = _property_name(name)
        if not name: return
        priority = str(dom_string(priority)).strip().lower()
        if priority and priority != "important": return
        value = _normalize_value(value); priority = "important" if priority == "important" else ""
        declarations = [item for item in self._declarations() if item[0] != name]
        if value: declarations.append((name, value, priority))
        self._write(declarations)
    def _value(self, name):
        name = _property_name(name)
        return next((v for n, v, _ in self._declarations() if n == name), "")
    def get_cssText(self):
        return "".join(f"{n}: {v}{' !important' if p else ''}; " for n, v, p in self._declarations()).strip()
    def set_cssText(self, value):
        self._write(parse_declarations(dom_string(value)))
    def get_length(self): return len(self._declarations())
    def get_parentRule(self): return self._parent_rule or pyv8.Null
    def get_cssFloat(self): return self._value("float")
    def set_cssFloat(self, value): self._set("float", value)
    def fn_getPropertyPriority(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "getPropertyPriority", 1, 0)
        name = _property_name(name); return next((p for n, _, p in self._declarations() if n == name), "")
    def fn_getPropertyValue(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "getPropertyValue", 1, 0)
        return self._value(name)
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING: raise missing_error("CSSStyleDeclaration", "item", 1, 0)
        index = integer(index); declarations = self._declarations()
        return declarations[index][0] if 0 <= index < len(declarations) else ""
    def fn_removeProperty(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "removeProperty", 1, 0)
        name = _property_name(name)
        declarations = self._declarations()
        old = next((v for n, v, _ in declarations if n == name), "")
        self._write([item for item in declarations if item[0] != name]); return old
    def fn_setProperty(self, name=MISSING, value=MISSING, priority="", *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "setProperty", 2, 0)
        if value is MISSING: raise missing_error("CSSStyleDeclaration", "setProperty", 2, 1)
        self._set(name, value, priority)
    def _named(self, name):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return None
        value = self._value(dashed)
        if value: return value
        # A supported but undeclared property reads as ""; an unknown name stays undefined.
        return "" if supported(dashed) else None
    def _exists(self, name):
        """Whether the engine resolves this name to a property at all."""
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return False
        if supported(dashed): return True
        declared = _property_name(dashed)
        return any(_property_name(declared_name) == declared for declared_name, _, _ in self._declarations())
    def __v8_name_get__(self, name): return self._named(name)
    def __v8_name_set__(self, name, value):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return None
        self._set(dashed, value); return True
    def __v8_name_query__(self, name):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        return 0 if self._exists(name) else None
    def __v8_name_del__(self, name):
        if isinstance(name, str) and name not in _OBJECT_NAMES: self._set(_dash_name(name), "")
        return True
    def __v8_name_enum__(self):
        """Every supported property, in the engine's own order.

        Declaring one changes no position and a custom property is not listed at all.
        """
        return list(_CAMEL_NAMES)
    def __v8_index_get__(self, index): return self.fn_item(index)
    def __v8_index_set__(self, index, value): return None
    def __v8_index_query__(self, index): return V8Attribute.ReadOnly if self.fn_item(index) else None
    def __v8_index_del__(self, index): return None
    def __v8_index_enum__(self): return self.get_length()


@interface('CSS', None)
class CSS:
    """The CSSOM entry point: one question a page asks before it writes a rule."""

    @staticmethod
    def fn_supports(_realm, property=MISSING, value=MISSING, *_extra):
        """Whether a property is one this tree resolves names for.

        One argument is a whole declaration, as in ``CSS.supports("color: red")``; two
        are a property and a value. Arguments are coerced to strings and anything
        unparseable or unknown answers false rather than throwing, which is what
        Chrome does with a number or a missing argument. A value is only checked for
        being there, because a grammar per property is not modelled.
        """
        if value is MISSING:
            declarations = parse_declarations(_text(property))
            return bool(declarations) and all(supported(name)
                                              for name, _, _ in declarations)
        return supported(_text(property)) and bool(_text(value).strip())


def _text(value):
    if value is MISSING:
        return ""
    return value if isinstance(value, str) else dom_string(value)


def stylesheet_source(node):
    """The CSS text an owner element carries.

    A ``<style>`` holds its own text; a ``<link rel=stylesheet>`` holds what the
    fetch stored on it, which is nothing until the load settles.
    """
    name = str(getattr(node, "localName", "") or "").lower()
    if name == "link":
        rel = str(getattr(node, "attributes", {}).get("rel", "")).lower().split()
        if "stylesheet" not in rel:
            return None
        return getattr(node, "_stylesheet_text", "") or ""
    if name == "style":
        # The element's text is authoritative; ``_raw_text`` only covers a style the
        # parser never closed (see ``parser._close_element``).
        return node.get_textContent() or getattr(node, "_raw_text", "")
    return None


@interface('StyleSheet', None)
class StyleSheet:
    """One stylesheet as a page sees it: where it came from, and what it is for."""

    def __init__(self, owner=None, document=None, **_):
        self._owner, self._document = owner, document

    def get_type(self): return "text/css"
    def get_href(self):
        """Where a linked sheet came from; a ``<style>`` element has no URL."""
        if self._owner is None or str(getattr(self._owner, "localName", "")).lower() != "link":
            return pyv8.Null
        href = self._owner.attributes.get("href")
        if href is None:
            return pyv8.Null
        return urljoin(getattr(self._document, "url", "") or "", str(href))
    def get_title(self):
        title = None if self._owner is None else self._owner.attributes.get("title")
        return pyv8.Null if title is None else str(title)
    def get_disabled(self):
        return bool(self._owner is not None and "disabled" in self._owner.attributes)
    def set_disabled(self, value):
        if self._owner is None:
            return
        if boolean(value):
            self._owner.attributes["disabled"] = ""
        else:
            self._owner.attributes.pop("disabled", None)
    def get_ownerNode(self):
        return pyv8.Null if self._owner is None else self._owner
    def get_parentStyleSheet(self):
        return pyv8.Null


@interface('CSSStyleSheet', 'StyleSheet')
class CSSStyleSheet(StyleSheet):
    """A sheet of CSS rules: the owner it came from, and the rules themselves."""

    def _owner_sheet(self): return self

    def get_cssRules(self):
        """The sheet's rules, read from the owner's text and then the page's own.

        The text is what the parse is keyed on, so a rule the page inserted survives
        until the sheet's own text changes -- at which point Chrome re-parses too.
        """
        from .css_rules import CSSRuleList, build_rules
        owner = getattr(self, "_owner", None)
        text = "" if owner is None else stylesheet_source(owner)
        text = "" if text is None else text
        if getattr(self, "_rule_text", None) != text:
            self._rule_text = text
            self._rules = build_rules(text, self)
        return CSSRuleList(self._rules)

    def fn_insertRule(self, rule=MISSING, index=0, *_extra):
        from .css_rules import insert_rule
        if rule is MISSING:
            raise missing_error('CSSStyleSheet', 'insertRule', 2, 0, constructor=False)
        return insert_rule(self, str(dom_string(rule)), integer(index))

    def fn_deleteRule(self, index=MISSING, *_extra):
        from .css_rules import RANGE_MESSAGE
        if index is MISSING:
            raise missing_error('CSSStyleSheet', 'deleteRule', 1, 0, constructor=False)
        rules = self.get_cssRules()._rules
        position = integer(index)
        if not 0 <= position < len(rules):
            raise IndexSizeError(RANGE_MESSAGE)
        del rules[position]
        return None


@interface('StyleSheetList', None)
class StyleSheetList(ReadOnlyIndexedPropertyMixin, LegacyNamedPropertyMixin):
    """The document's sheets, live: ``<style>`` elements and the ``<link>`` elements
    that are stylesheets, in document order."""

    def __init__(self, document=None, **_):
        self._document = document

    def _owners(self):
        owners = []
        for node in self._document._descendants():
            name = str(getattr(node, "localName", "") or "").lower()
            rel = str(getattr(node, "attributes", {}).get("rel", "")).lower().split()
            if name == "style" or (name == "link" and "stylesheet" in rel):
                owners.append(node)
        return owners
    def _sheet_for(self, node):
        """The sheet for one owner, kept on the node so a second read is the same
        object, as it is in Chrome.
        """
        sheet = getattr(node, "_style_sheet", None)
        if sheet is None:
            sheet = node._style_sheet = CSSStyleSheet(node, self._document)
        return sheet
    def get_length(self): return len(self._owners())
    def _indexed_items(self): return [self._sheet_for(node) for node in self._owners()]
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('StyleSheetList', 'item', 1, 0, constructor=False)
        items = self._indexed_items()
        position = int(index)
        return items[position] if 0 <= position < len(items) else pyv8.Null
    def _supported_names(self):
        return [str(node.attributes["id"]) for node in self._owners()
                if node.attributes.get("id")]
    def _named_item(self, name):
        for node in self._owners():
            if node.attributes.get("id") == name:
                return self._sheet_for(node)
        return pyv8.Null

__all__ = ["CSS", "CSSStyleDeclaration", "CSSStyleSheet", "StyleSheet", "StyleSheetList",
           "SUPPORTED_NAMES", "parse_declarations", "stylesheet_source", "supported"]
