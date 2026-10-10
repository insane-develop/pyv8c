"""``DOMParser``: parsing markup into a document that no window owns.

An HTML parse builds the same skeleton a navigation does, with scripts and
resources switched off; an XML parse keeps namespaces, whitespace and comments
as written, and answers a document carrying a ``parsererror`` element instead of
throwing when the markup does not parse.
"""
import re


from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from ..page_errors import PageTypeError
from .character_data import Comment, Text
from .document import HTMLDocument, XMLDocument, detached_services
from .parser import IncrementalDOMParser
from .utils import XHTML_NAMESPACE


#: The ``SupportedType`` enum: Chrome takes these five spellings and nothing else,
#: so a parameter, a case difference or surrounding space is a TypeError.
SUPPORTED = ("text/html", "text/xml", "application/xml", "application/xhtml+xml",
             "image/svg+xml")

_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}

_ATTRIBUTE = re.compile(r"([^\s=/>]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_START_TAG = re.compile(
    r"<\s*([^\s>/]+)((?:\s+[^\s=/>]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'))?)*)\s*(/?)>", re.S)
_END_TAG = re.compile(r"</\s*([^\s>/]+)[^>]*>")


def _caller(services=None):
    """The document this parse is asked from, which lends its realm and its URL."""
    document = getattr(getattr(services, "document_services", None), "document", None)
    if document is None:
        from ..bom.window import Window
        window = Window._accessor_window()
        services = getattr(window, "_services", None) if window is not None else None
        document = getattr(getattr(services, "document_services", None), "document", None)
    if document is None:
        return None, "about:blank"
    return getattr(document, "_services", None), document.get_URL()


def _text(value):
    """The character data of *value*, entities and character references resolved."""
    def replace(match):
        body = match.group(1)
        try:
            if body[:1] == "#":
                digits = body[2:] if body[1:2] in ("x", "X") else body[1:]
                return chr(int(digits, 16 if body[1:2] in ("x", "X") else 10))
        except ValueError:
            return match.group(0)
        return _ENTITIES.get(body, match.group(0))
    return re.sub(r"&([^;&]*);", replace, value)


def _split(name):
    prefix, _, local = str(name).partition(":")
    return (prefix, local) if local else (None, str(name))


def _qualified(element):
    prefix, local = getattr(element, "prefix", None), getattr(element, "localName", "") or ""
    return f"{prefix}:{local}" if prefix else local


def _attributes(raw):
    found = []
    for match in _ATTRIBUTE.finditer(raw):
        quoted = match.group(2) if match.group(2) is not None else match.group(3)
        found.append((match.group(1), _text(quoted or "")))
    return found


class _XmlTree:
    """A small XML parser: elements, attributes, namespaces, text and comments."""

    def __init__(self, services, document):
        self.services, self.document = services, document
        self.fault = None

    def parse(self, source):
        self.source, self.pos, self.stack = str(source), 0, []
        self.scopes = [{}]
        while self.pos < len(self.source) and self.fault is None:
            if self.source[self.pos] != "<":
                self._characters()
            elif self.source.startswith("<!--", self.pos):
                self._comment()
            elif self.source.startswith("<![CDATA[", self.pos):
                self._cdata()
            elif self.source.startswith(("<?", "<!"), self.pos):
                self._skip()
            elif self.source.startswith("</", self.pos):
                self._end()
            else:
                self._start()
        if self.fault is not None:
            self._report()
        return self.document

    def _current(self):
        return self.stack[-1] if self.stack else None

    def _append(self, node):
        (self._current() or self.document).append_child(node)

    def _characters(self):
        end = self.source.find("<", self.pos)
        end = len(self.source) if end < 0 else end
        raw, self.pos = self.source[self.pos:end], end
        if raw and self._current() is not None:
            self._append(Text(_text(raw), services=self.services))

    def _comment(self):
        end = self.source.find("-->", self.pos + 4)
        if end < 0:
            self.fault = "unclosed comment"
            return
        self._append(Comment(self.source[self.pos + 4:end], services=self.services))
        self.pos = end + 3

    def _cdata(self):
        end = self.source.find("]]>", self.pos + 9)
        if end < 0:
            self.fault = "unclosed CDATA section"
            return
        if self._current() is not None:
            self._append(Text(self.source[self.pos + 9:end], services=self.services))
        self.pos = end + 3

    def _skip(self):
        tail = "?>" if self.source.startswith("<?", self.pos) else ">"
        end = self.source.find(tail, self.pos + 2)
        self.pos = len(self.source) if end < 0 else end + len(tail)

    def _start(self):
        match = _START_TAG.match(self.source, self.pos)
        if not match:
            self.fault = "unclosed start tag"
            return
        name, attrs, closed = match.group(1), _attributes(match.group(2)), match.group(3)
        self.pos = match.end()
        scope = dict(self.scopes[-1])
        for attribute, value in attrs:
            if attribute == "xmlns":
                scope[""] = value
            elif attribute.startswith("xmlns:"):
                scope[attribute[6:]] = value
        prefix, local = _split(name)
        element = self.document._new_element_ns(scope.get(prefix or ""), prefix, local, name)
        for attribute, value in attrs:
            element.attributes[attribute] = value
        self._append(element)
        if not closed:
            self.stack.append(element)
            self.scopes.append(scope)

    def _end(self):
        match = _END_TAG.match(self.source, self.pos)
        if not match:
            self.fault = "unclosed end tag"
            return
        current = self._current()
        if current is None or _qualified(current) != match.group(1):
            self.fault = f"mismatched end tag: </{match.group(1)}>"
            return
        self.pos = match.end()
        self.stack.pop()
        self.scopes.pop()

    def _report(self):
        """Chrome answers the partial document with a ``parsererror`` put in it."""
        element = self.document._new_element_ns(XHTML_NAMESPACE, None, "parsererror",
                                                "parsererror")
        element.attributes["xmlns"] = XHTML_NAMESPACE
        element.append_child(Text(self.fault or "parse error", services=self.services))
        self._append(element)


@interface('DOMParser', None)
class DOMParser:
    def __init__(self, *_extra, services=None, **_):
        self._services = services

    def fn_parseFromString(self, type=MISSING, mime=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('DOMParser', 'parseFromString', 2, 0, constructor=False)
        if mime is MISSING:
            raise missing_error('DOMParser', 'parseFromString', 2, 1, constructor=False)
        source, kind = dom_string(type), dom_string(mime)
        if kind not in SUPPORTED:
            raise PageTypeError(
                "Failed to execute 'parseFromString' on 'DOMParser': The provided value"
                f" '{kind}' is not a valid enum value of type SupportedType.")
        services, url = _caller(self._services)
        document = (HTMLDocument if kind == "text/html" else XMLDocument)(
            services=services, url=url, ready_state="complete", content_type=kind)
        document._services = detached_services(services, document)
        if kind == "text/html":
            IncrementalDOMParser(document._services, document, source, active=False).run()
            return document
        return _XmlTree(document._services, document).parse(source)

__all__ = ["DOMParser", "SUPPORTED"]
