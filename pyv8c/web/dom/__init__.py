"""The flat DOM namespace: this package re-exports the sibling modules."""

from .character_data import CharacterData, Comment, DocumentType, Text
from .collections import (
    DOMTokenList,
    HTMLAllCollection,
    HTMLCollection,
    NamedNodeMap,
    NodeList,
)
from .document import Document
from .events import (
    CustomEvent,
    Event,
    EventTarget,
    KeyboardEvent,
    MouseEvent,
    UIEvent,
)
from .html_classes import HTML_CLASSES
from .html_elements import (
    Element,
    HTMLElement,
    HTMLImageElement,
    HTMLLinkElement,
    HTMLMetaElement,
    HTMLScriptElement,
    HTMLStyleElement,
    HTMLTemplateElement,
)
from .html_iframe import HTMLIFrameElement
from .html_media import HTMLAudioElement, HTMLMediaElement, HTMLVideoElement
from .nodes import Attr, DocumentFragment, Node

__all__ = [
    "NodeList", "HTMLCollection", "HTMLAllCollection", "DOMTokenList",
    "Event", "UIEvent", "MouseEvent", "KeyboardEvent", "CustomEvent", "EventTarget",
    "Node", "DocumentFragment", "Attr", "NamedNodeMap", "CharacterData", "Text",
    "Comment", "DocumentType", "Element", "HTMLElement", "HTML_CLASSES",
    "HTMLImageElement", "HTMLScriptElement", "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement",
    "HTMLTemplateElement", "HTMLIFrameElement",
    "HTMLMediaElement", "HTMLAudioElement", "HTMLVideoElement", "Document",
]
