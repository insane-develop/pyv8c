"""The HTML local-name to interface-class registry.  It sits above the element
modules, which only need the node model below."""

from .html_canvas import HTMLCanvasElement
from .html_elements import (
    HTMLAnchorElement,
    HTMLButtonElement,
    HTMLImageElement,
    HTMLElement,
    HTMLLabelElement,
    HTMLLinkElement,
    HTMLMetaElement,
    HTMLScriptElement,
    HTMLStyleElement,
    HTMLTableCellElement,
    HTMLTableElement,
    HTMLTableRowElement,
    HTMLTemplateElement,
    HTMLUnknownElement,
    _html_class,
)
from .html_form import HTMLFormElement
from .html_iframe import HTMLIFrameElement
from .html_input import (
    HTMLInputElement,
    HTMLOptionElement,
    HTMLSelectElement,
    HTMLTextAreaElement,
)
from .html_media import HTMLAudioElement, HTMLMediaElement, HTMLVideoElement
from .html_tag_interfaces import TAG_INTERFACES


#: IdlInterface name -> implementation class, for every modelled interface.
HTML_CLASSES = {
    **{name: _html_class(name) for name in (
        "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement",
        "HTMLSpanElement")},
    #: The interfaces the measured tag table asks for that have nothing hand-written.
    **{name: _html_class(name) for name in (
        "HTMLAreaElement", "HTMLBRElement", "HTMLBaseElement", "HTMLDListElement",
        "HTMLDataElement", "HTMLDataListElement", "HTMLDetailsElement", "HTMLDialogElement",
        "HTMLDirectoryElement", "HTMLEmbedElement", "HTMLFieldSetElement", "HTMLFontElement",
        "HTMLFrameElement", "HTMLFrameSetElement", "HTMLHRElement", "HTMLHeadingElement",
        "HTMLLIElement", "HTMLLegendElement", "HTMLMapElement", "HTMLMarqueeElement",
        "HTMLMenuElement", "HTMLMeterElement", "HTMLModElement", "HTMLOListElement",
        "HTMLObjectElement", "HTMLOptGroupElement", "HTMLOutputElement",
        "HTMLParagraphElement", "HTMLParamElement", "HTMLPictureElement", "HTMLPreElement",
        "HTMLProgressElement", "HTMLQuoteElement", "HTMLSlotElement", "HTMLSourceElement",
        "HTMLTableCaptionElement", "HTMLTableColElement", "HTMLTableSectionElement",
        "HTMLTimeElement", "HTMLTitleElement", "HTMLTrackElement", "HTMLUListElement")},
    "HTMLElement": HTMLElement,
    # Interfaces with members of their own carry a hand-written class.
    "HTMLFormElement": HTMLFormElement,
    "HTMLButtonElement": HTMLButtonElement,
    "HTMLLabelElement": HTMLLabelElement,
    "HTMLMediaElement": HTMLMediaElement,
    "HTMLAudioElement": HTMLAudioElement,
    "HTMLVideoElement": HTMLVideoElement,
    "HTMLTableElement": HTMLTableElement,
    "HTMLTableRowElement": HTMLTableRowElement,
    "HTMLTableCellElement": HTMLTableCellElement,
    "HTMLAnchorElement": HTMLAnchorElement,
    "HTMLTextAreaElement": HTMLTextAreaElement,
    "HTMLOptionElement": HTMLOptionElement,
    "HTMLSelectElement": HTMLSelectElement,
    "HTMLUnknownElement": HTMLUnknownElement,
    "HTMLImageElement": HTMLImageElement, "HTMLScriptElement": HTMLScriptElement, "HTMLCanvasElement": HTMLCanvasElement,
    "HTMLLinkElement": HTMLLinkElement, "HTMLStyleElement": HTMLStyleElement,
    "HTMLMetaElement": HTMLMetaElement, "HTMLTemplateElement": HTMLTemplateElement,
    "HTMLIFrameElement": HTMLIFrameElement, "HTMLInputElement": HTMLInputElement,
    # Chromium also resolves the historical lowercase spelling.
    "HTMLIframeElement": HTMLIFrameElement,
}


#: Local name -> implementation class, from the measured tag table.
TAG_CLASSES = {tag: HTML_CLASSES[name]
               for name, tags in TAG_INTERFACES.items() for tag in tags}


def _unknown_or_custom(name):
    """What Chrome answers a name it has no interface for.

    A hyphenated name is a valid custom element name, so an undefined one is a plain
    ``HTMLElement``; anything else is the :class:`HTMLUnknownElement` it exposes.
    """
    return HTML_CLASSES["HTMLElement" if "-" in name else "HTMLUnknownElement"]


def class_for_tag(tag):
    """The implementation class for an HTML local name."""
    name = str(tag).lower()
    return TAG_CLASSES.get(name) or _unknown_or_custom(name)


def class_for_local_name(local_name):
    """The class for a local name *as written*: ``createElementNS`` does not lower-case
    the name it was given, so ``DIV`` is an unknown element to it.
    """
    name = str(local_name)
    return TAG_CLASSES.get(name) or _unknown_or_custom(name)


#: The SVG local names whose interface is not their own capitalised name.
_SVG_NAMES = {"svg": "SVGSVGElement", "tspan": "SVGTSpanElement"}


def svg_interface_name(local_name):
    """The interface Chrome gives an SVG local name, matched case for case.

    Whether the realm declares it is its question: Chrome answers ``SVGElement`` for a
    name it has none for, and so does the caller when this one is not in the baseline.
    The filter primitives keep a capital ``FE`` rather than the name's own ``fe``.
    """
    match = _SVG_NAMES.get(local_name)
    if match is not None:
        return match
    capital = local_name[:2].upper() + local_name[2:] if local_name.startswith("fe") \
        else local_name[:1].upper() + local_name[1:]
    return "SVG" + capital + "Element"

__all__ = ["HTML_CLASSES", "TAG_CLASSES", "class_for_local_name", "class_for_tag",
           "svg_interface_name"]
