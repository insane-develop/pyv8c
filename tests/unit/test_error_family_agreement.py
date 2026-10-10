"""The native family and the page's vocabulary are one family, checked, not assumed.

The binding publishes ``pyv8.JsError`` and the six beside it from its own table; the
page vocabulary subclasses each of them.  Neither side reads the other, so what keeps
them in step is this: for every type the binding publishes, the vocabulary holds a class
that inherits it -- and nothing in the vocabulary claims a family it does not put in
its ``VISIBLE``.
"""
import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

import pyv8

from pyv8c.web import page_errors as vocabulary

#: Each JavaScript error the binding publishes, and the vocabulary class that inherits
#: the type standing for it.
PUBLISHED = (
    ("Error", pyv8.JsError, "PageError"),
    ("TypeError", pyv8.JsTypeError, "PageTypeError"),
    ("RangeError", pyv8.JsRangeError, "PageRangeError"),
    ("ReferenceError", pyv8.JsReferenceError, "PageReferenceError"),
    ("SyntaxError", pyv8.JsSyntaxError, "PageSyntaxError"),
    ("URIError", pyv8.JsURIError, "PageURIError"),
    ("EvalError", pyv8.JsEvalError, "PageEvalError"),
)


def test_every_published_family_member_has_a_page_class():
    for js_name, base, page_name in PUBLISHED:
        page_class = getattr(vocabulary, page_name, None)
        assert isinstance(page_class, type), js_name
        assert issubclass(page_class, base), js_name


def test_the_page_holds_no_family_class_it_cannot_account_for():
    """A class that inherits a family member is something a realm may be shown, so it
    belongs in ``VISIBLE``; one outside it would be a declaration that does its work
    without saying so.
    """
    strays = [
        name for name, value in vars(vocabulary).items()
        if isinstance(value, type) and issubclass(value, pyv8.JsError)
        and name not in vocabulary.VISIBLE
    ]
    assert not strays, strays
