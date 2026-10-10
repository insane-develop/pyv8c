"""The errors a realm is shown, and the names they answer to there.

Everything here is something JavaScript may see.  A class joins the hierarchy by
inheriting the binding's own error type -- ``pyv8.JsTypeError`` and the six beside it --
so what a realm calls it follows from what it inherited, and a class that inherits none
of them is a failure of the host's own, which no realm is ever shown.  A realm
constructor that is not one of the standard names is declared on the class itself.

The seven classes of the family are as much for the application as for this package: a host
that raises :class:`PageReferenceError` shows a realm a ``ReferenceError``.

Nothing host-only lives here: that is :mod:`pyv8c.foundation.host_errors`.
"""
import pyv8

from ..idl import interface
from ..foundation.error_policy import is_declared
from ..foundation.logging import logger


def _error_text(name, message):
    """What ``Error.prototype.toString`` produces for the same pair."""
    if not name:
        return message
    return f"{name}: {message}" if message else name


class PageError(pyv8.JsError):
    """A failure a realm may be shown: the page sees an ``Error``."""


class PageTypeError(pyv8.JsTypeError):
    """A failure a realm may be shown: the page sees a ``TypeError``."""


class PageRangeError(pyv8.JsRangeError):
    """A failure a realm may be shown: the page sees a ``RangeError``."""


class PageReferenceError(pyv8.JsReferenceError):
    """A failure a realm may be shown: the page sees a ``ReferenceError``."""


class PageSyntaxError(pyv8.JsSyntaxError):
    """A failure a realm may be shown: the page sees a ``SyntaxError``."""


class PageURIError(pyv8.JsURIError):
    """A failure a realm may be shown: the page sees a ``URIError``."""


class PageEvalError(pyv8.JsEvalError):
    """A failure a realm may be shown: the page sees an ``EvalError``."""


class LegacyPropertyError(PageTypeError, pyv8.JsInterceptorFailure):
    """A legacy interceptor failure that is silent outside strict mode.

    Inheriting the marker beside the error is the whole declaration: a realm sees a
    ``TypeError``, and a sloppy write or delete that fails this way goes unraised where
    V8 allows it.
    """

    @classmethod
    def named(cls, operation, name, interface, detail):
        """Build Chromium's named-property exception message."""
        preposition = "on" if operation == "set" else "from"
        return cls(
            f"Failed to {operation} a named property '{name}' {preposition} "
            f"'{interface}': {detail}"
        )

    @classmethod
    def indexed(cls, operation, index, interface, detail):
        """Build Chromium's indexed-property exception message."""
        preposition = "on" if operation == "set" else "from"
        return cls(
            f"Failed to {operation} an indexed property [{index}] {preposition} "
            f"'{interface}': {detail}"
        )


#: What each DOMException name reports through the legacy ``code`` accessor.  The
#: names a browser gives code 0 are listed with their 0 rather than left out, so every
#: name pyv8c raises is answerable here: ``NotAllowedError``, ``EncodingError``,
#: ``UnknownError`` and the ``RangeError`` a decoder reports have no legacy number.
DOM_EXCEPTION_CODES = {
    "IndexSizeError": 1,
    "DOMStringSizeError": 2,
    "HierarchyRequestError": 3,
    "WrongDocumentError": 4,
    "InvalidCharacterError": 5,
    "NoDataAllowedError": 6,
    "NoModificationAllowedError": 7,
    "NotFoundError": 8,
    "NotSupportedError": 9,
    "InUseAttributeError": 10,
    "InvalidStateError": 11,
    "SyntaxError": 12,
    "InvalidModificationError": 13,
    "NamespaceError": 14,
    "InvalidAccessError": 15,
    "ValidationError": 16,
    "TypeMismatchError": 17,
    "SecurityError": 18,
    "NetworkError": 19,
    "AbortError": 20,
    "URLMismatchError": 21,
    "QuotaExceededError": 22,
    "TimeoutError": 23,
    "InvalidNodeTypeError": 24,
    "DataCloneError": 25,
    "NotAllowedError": 0,
    "EncodingError": 0,
    "UnknownError": 0,
    "RangeError": 0,
}


class _NamedError:
    """What both DOM error objects are made of: a message and a name, each accepting
    whatever a realm's callback handed over.

    The conversion is a member's own, so it lives where the values arrive; a plain
    string passes through, and the undefined a realm passes for an omitted argument
    becomes what a browser would say for it.  Note what this mixin does *not* do: it
    does not inherit the family, so a class that takes it alone stays the host's own,
    which is exactly what ``DOMError`` is.
    """

    def _set(self, message, name):
        if not isinstance(message, str) or not isinstance(name, str):
            from ..foundation.arguments import dom_string

            message = "" if message is pyv8.Undefined else dom_string(message)
            name = "Error" if name is pyv8.Undefined else dom_string(name)
        self._message = message
        self._name = name
        Exception.__init__(self, self._message)

    def __str__(self):
        return _error_text(self._name, self._message)

    def get_name(self): return self._name
    def get_message(self): return self._message


@interface("DOMException", None)
class DOMException(_NamedError, PageError):
    """A browser-visible DOMException.

    Inheriting ``PageError`` is what lets a realm see it, and its shape is declared here
    because a realm builds one with its own ``DOMException`` constructor: the message
    first, then the name.  The legacy numeric ``code`` is derived from
    :data:`DOM_EXCEPTION_CODES` rather than duplicated as class constants; scripts read
    it through the ``code`` accessor the document states, on the interface the core
    feature declares (``web/features/core.py``).
    """

    __v8_error_shape__ = ("DOMException", ("_name",), (("_code", "code"),))

    def __init__(self, message="", name="Error", *_extra, code=None, **_):
        self._set(message, name)
        self._code = int(DOM_EXCEPTION_CODES.get(self._name, 0) if code is None else code)

    def get_code(self): return self._code


@interface("DOMError", None)
class DOMError(_NamedError, Exception):
    """The older of the two error objects, with no legacy ``code`` to report.  A realm
    may hold one, but it is the host's own failure: it inherits nothing of the family,
    so no boundary ever shows it."""

    def __init__(self, message="", name="Error", *_extra, **_):
        self._set(message, name)


def dom_exception(name, message=""):
    """Create a fresh DOMException with the standard legacy code."""
    return DOMException(message=message, name=name)


def _factory(name):
    return lambda message="": dom_exception(name, message)


IndexSizeError = _factory("IndexSizeError")
InvalidCharacterError = _factory("InvalidCharacterError")
HierarchyRequestError = _factory("HierarchyRequestError")
NotFoundError = _factory("NotFoundError")
NotSupportedError = _factory("NotSupportedError")
InvalidStateError = _factory("InvalidStateError")
InvalidAccessError = _factory("InvalidAccessError")
SecurityError = _factory("SecurityError")
NetworkError = _factory("NetworkError")
AbortError = _factory("AbortError")
TimeoutError = _factory("TimeoutError")
QuotaExceededError = _factory("QuotaExceededError")
DataCloneError = _factory("DataCloneError")


def js_error_value(exc):
    """What a rejection should carry for *exc*.

    A declared error crosses into JavaScript when the resolver settles it, and anything
    that is not an exception (a JavaScript value, a string) is a legitimate reason and
    crosses as itself.  A host failure is neither: a rejection still has to carry
    something, and what it carries is the page's to read, so the page gets a plain error
    with generic text while the host's own reason stays in the log.
    """
    if isinstance(exc, pyv8.JSException):
        return exc.original
    if isinstance(exc, BaseException) and not is_declared(exc):
        logger.debug("the host rejected a promise: {}", exc)
        return PageError("The operation failed")
    return exc


#: What this module offers that a realm may be shown, stated rather than left to be
#: inferred: the seven classes of the family, the legacy interceptor failure that is a
#: ``TypeError`` to a realm, the ``DOMException`` it builds, the thirteen factories that
#: make one by name, and the maker itself.  ``DOMError`` is deliberately absent -- it is
#: the host's own, and nothing here that carries data or a policy belongs in this list.
VISIBLE = (
    "PageError", "PageTypeError", "PageRangeError", "PageReferenceError", "PageSyntaxError",
    "PageURIError", "PageEvalError", "LegacyPropertyError", "DOMException",
    "IndexSizeError", "InvalidCharacterError", "HierarchyRequestError", "NotFoundError",
    "NotSupportedError", "InvalidStateError", "InvalidAccessError", "SecurityError",
    "NetworkError", "AbortError", "TimeoutError", "QuotaExceededError", "DataCloneError",
    "dom_exception",
)

__all__ = [
    "PageError", "PageTypeError", "PageRangeError", "PageReferenceError", "PageSyntaxError",
    "PageURIError", "PageEvalError", "DOMException", "DOMError", "DOM_EXCEPTION_CODES",
    "LegacyPropertyError", "dom_exception",
    "IndexSizeError", "InvalidCharacterError", "HierarchyRequestError", "NotFoundError",
    "NotSupportedError", "InvalidStateError", "InvalidAccessError", "SecurityError",
    "NetworkError", "AbortError", "TimeoutError", "QuotaExceededError", "DataCloneError",
    "js_error_value",
]
