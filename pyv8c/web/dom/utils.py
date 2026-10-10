"""Shared DOM helpers and the metadata every legacy Web IDL collection uses."""

import re
from urllib.parse import urlsplit, urlunsplit

from pyv8.flag import V8Attribute

from ...foundation.arguments import dom_string
from ...foundation.urls import form_urlencoded


#: XML-compatible qualified name, shared by the element/attribute-creating APIs.
_VALID_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")


#: The namespace HTML documents use, and the two foreign ones ``createElementNS`` maps
#: to interfaces of their own.
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"


#: A name starts with a letter, an underscore or anything above ASCII, and holds none of
#: these anywhere: Chromium's rule, which takes ``a=b`` and ``äx`` as names and ``a b``
#: as not one.
_NAME_START = re.compile(r"[A-Za-z_\u0080-\U0010ffff]")
_NAME_FORBIDDEN = frozenset(" \t\n\f\r/>\x00")


def name_fault(value):
    """What is wrong with *value* as a name -- ``("start", c)`` or ``("character", c)``
    -- and ``None`` when nothing is.  Chromium names the character, so the caller can.
    """
    text = str(value)
    if not text:
        return "start", ""
    if not _NAME_START.match(text[0]):
        return "start", text[0]
    invalid = next((character for character in text if character in _NAME_FORBIDDEN),
                   None)
    return None if invalid is None else ("character", invalid)


#: What a collection borrows from ``Array.prototype``: ``IdlSpec.array`` and the baseline's
#: ``template.array`` both take ``"iterator"`` or ``"iterable"``; the bits are the binding's.


#: ``Object.prototype`` string keys (plus ``__proto__``) that named properties never shadow.
OBJECT_PROTOTYPE_NAMES = frozenset((
    "constructor", "__defineGetter__", "__defineSetter__", "hasOwnProperty",
    "__lookupGetter__", "__lookupSetter__", "isPrototypeOf",
    "propertyIsEnumerable", "toString", "valueOf", "__proto__",
    "toLocaleString",
))


#: Descriptor flags shared by every legacy named property.
NAMED_PROPERTY_ATTRIBUTES = V8Attribute.ReadOnly | V8Attribute.DontEnum


def dash_name(name):
    """``fooBar`` -> ``foo-bar``; serves both CSS names and the ``data-*`` attribute.
    A name already starting with ``--`` is its own dashed spelling."""
    if name.startswith("--"):
        return name
    return re.sub(r"([A-Z])", lambda match: "-" + match.group(1).lower(), name)


def camel_name(name):
    """``foo-bar`` -> ``fooBar``; ``""`` when a trailing dash cannot be spelled back."""
    if name.startswith("--"):
        return name
    result = name
    while "-" in result:
        left, right = result.split("-", 1)
        if not right:
            return ""
        result = left + right[0].upper() + right[1:]
    return result


def as_nodes(values, parent):
    """Convert a Web IDL ``(Node or DOMString)...`` argument list to nodes."""
    # Local import: the node model imports this module, so a module-level import would cycle.
    from .character_data import Text
    from .nodes import Node

    services = getattr(parent, "_services", None)
    return [
        value if isinstance(value, Node) else Text(dom_string(value), services=services)
        for value in values
    ]

_element_type = None


def is_element(node):
    """Whether ``node`` is a DOM ``Element``; the class is resolved lazily because
    ``html_elements`` sits above the node model."""
    global _element_type
    if _element_type is None:
        from .html_elements import Element

        _element_type = Element
    return isinstance(node, _element_type)


#: The three encodings ``<form enctype>`` selects between.
URLENCODED = "application/x-www-form-urlencoded"
MULTIPART = "multipart/form-data"
TEXT_PLAIN = "text/plain"


#: Chromium's multipart boundary prefix, followed by sixteen random characters.
_BOUNDARY_PREFIX = "----WebKitFormBoundary"
_BOUNDARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def crlf(text):
    """*text* with every line ending normalized to CRLF."""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def replace_query(url, query):
    """*url* with its query replaced by *query*; the ``?`` stays even when empty."""
    parts = urlsplit(str(url))
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return f"{base}?{query}{fragment}"


def urlencoded(entries):
    """The ``application/x-www-form-urlencoded`` spelling of *entries*."""
    return form_urlencoded(entries)


def encode_entries(entries, enctype):
    """The ``(body, Content-Type)`` *entries* are posted as; the body is UTF-8 bytes."""
    if enctype == MULTIPART:
        # Lazy: multipart is rare, and this has no place on the startup path.
        import random

        boundary = _BOUNDARY_PREFIX + "".join(random.choices(_BOUNDARY_ALPHABET, k=16))
        return _multipart(entries, boundary), f"{MULTIPART}; boundary={boundary}"
    if enctype == TEXT_PLAIN:
        return _text_plain(entries), TEXT_PLAIN
    return urlencoded(entries).encode("utf-8"), URLENCODED


def _text_plain(entries):
    """Raw values, one CRLF-terminated line per entry."""
    return "".join(f"{name}={value}\r\n" for name, value in entries).encode("utf-8")


def _multipart(entries, boundary):
    """One part per entry, each with only a ``Content-Disposition`` header."""
    parts = []
    for name, value in entries:
        parts.append(f"--{boundary}\r\n"
                     f'Content-Disposition: form-data; name="{_escape(name)}"\r\n'
                     f"\r\n{value}\r\n")
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode("utf-8")


def _escape(name):
    """A part name with the characters that would break the header escaped."""
    return (str(name).replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22"))
