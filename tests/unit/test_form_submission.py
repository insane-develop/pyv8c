"""The entry list and the encodings a form submission uses, held to Chrome 153.

The expectations are transcriptions of a real browser's requests: the body
bytes and query strings in ``tests/integration/test_form_submission_on_the_wire``
were recorded from Chrome, and each rule here is one of theirs.
"""

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c.web.dom.html_form import Submission, entry_list, submission
from pyv8c.web.dom.utils import MULTIPART, TEXT_PLAIN, URLENCODED, encode_entries


class _Control:
    """A stand-in for one form control, with the members assembly reads."""

    def __init__(self, tag="input", *, name=None, value="", kind=None,
                 checked=False, disabled=False, attributes=None, options=()):
        self.localName = tag
        self.attributes = dict(attributes or {})
        if name is not None:
            self.attributes["name"] = name
        if kind is not None:
            self.attributes["type"] = kind
        if disabled:
            self.attributes["disabled"] = ""
        self._value = value
        self._checked = checked
        self._option_list = list(options)
        #: What the disabledness walk reads: a control with no ancestors.
        self.parentNode = None

    def get_value(self):
        """The value the element reports, with the browser's two special cases."""
        if self.localName == "select":
            return next((option.get_value() for option in self._option_list
                         if option.get_selected()), "")
        if self.get_type() in ("checkbox", "radio") and "value" not in self.attributes:
            return "on"
        return self._value
    def get_checked(self): return self._checked
    def get_type(self): return self.attributes.get("type", "text")
    def _options(self): return self._option_list


class _Option:
    def __init__(self, value, selected=False):
        self._value = value
        self._selected = selected

    def get_value(self): return self._value
    def get_selected(self): return self._selected


class _Form:
    """A form with a fixed control list, method, enctype and action."""

    def __init__(self, controls=(), *, method="post", enctype=None, action="http://h/echo"):
        self._controls_list = list(controls)
        self._method = method
        self._enctype = enctype
        self.attributes = {"method": method, "action": action}

    def _controls(self): return self._controls_list
    def get_method(self): return self._method
    def get_enctype(self): return self._enctype or URLENCODED
    def get_action(self): return self.attributes["action"]


def test_only_the_successful_controls_reach_the_entry_list():
    """Chrome's ``/kinds`` form, which sent ``t=v&on=on&s=o1&ta=text&b=bv``."""
    button = _Control("button", name="b", value="bv", kind="submit")
    textarea = _Control("textarea", name="ta", value="text")
    select = _Control("select", name="s", options=[_Option("o1", True), _Option("o2")])
    form = _Form([
        _Control(name="t", value="v"),
        _Control(name="on", kind="checkbox", checked=True),
        _Control(name="off", kind="checkbox"),
        _Control(name="dis", value="x", disabled=True),
        _Control(value="noname"),
        select,
        textarea,
        button,
    ])
    assert entry_list(form, button) == [
        ("t", "v"), ("on", "on"), ("s", "o1"), ("ta", "text"), ("b", "bv"),
    ]


def test_only_the_pressed_submit_button_contributes():
    pressed = _Control("button", name="one", value="1", kind="submit")
    other = _Control("button", name="two", value="2", kind="submit")
    form = _Form([pressed, other])
    assert entry_list(form, pressed) == [("one", "1")]
    # ``form.submit()`` has no submitter at all.
    assert entry_list(form, None) == []


def test_a_reset_button_never_contributes():
    reset = _Control("button", name="r", value="", kind="reset")
    assert entry_list(_Form([reset]), reset) == []


def test_a_multi_select_repeats_its_name():
    select = _Control("select", name="m", attributes={"multiple": ""}, options=[
        _Option("m1", True), _Option("m2", True), _Option("m3")])
    assert entry_list(_Form([select], method="get")) == [("m", "m1"), ("m", "m2")]


def test_a_file_input_contributes_nothing():
    assert entry_list(_Form([_Control(name="f", kind="file")])) == []


def test_a_textarea_reaches_the_wire_with_crlf():
    """Chrome sent ``ta=line1%0D%0Aline2`` for a two-line textarea."""
    form = _Form([_Control("textarea", name="ta", value="line1\nline2")])
    body, _ = encode_entries(entry_list(form), URLENCODED)
    assert body == b"ta=line1%0D%0Aline2"


def test_urlencoded_leaves_its_safe_set_and_encodes_the_rest():
    """Chrome sent ``a%7Eb*c%28d%29e_f-g.h%21i%27j&pct=100%25``."""
    body, content_type = encode_entries(
        [("safe", "a~b*c(d)e_f-g.h!i'j"), ("pct", "100%")], URLENCODED)
    assert content_type == URLENCODED
    assert body == b"safe=a%7Eb*c%28d%29e_f-g.h%21i%27j&pct=100%25"


def test_urlencoded_turns_a_space_into_a_plus_and_keeps_utf8_upper_case():
    body, _ = encode_entries([("sp", "hello world"), ("uni", "\u4e2d")], URLENCODED)
    assert body == b"sp=hello+world&uni=%E4%B8%AD"


def test_urlencoded_encodes_an_empty_list_as_nothing():
    assert encode_entries([], URLENCODED) == (b"", URLENCODED)


def test_text_plain_is_one_crlf_terminated_line_per_entry():
    body, content_type = encode_entries([("a", "1"), ("b", "2")], TEXT_PLAIN)
    assert content_type == TEXT_PLAIN
    assert body == b"a=1\r\nb=2\r\n"


def test_multipart_delimits_each_entry_with_the_boundary():
    body, content_type = encode_entries([("a", "1")], MULTIPART)
    prefix = "multipart/form-data; boundary="
    assert content_type.startswith(prefix)
    boundary = content_type[len(prefix):]
    assert boundary.startswith("----WebKitFormBoundary")
    assert len(boundary) == len("----WebKitFormBoundary") + 16
    assert body == (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="a"\r\n'
        f"\r\n1\r\n"
        f"--{boundary}--\r\n"
    ).encode()


def test_a_get_submission_replaces_the_actions_query():
    """Chrome landed on ``/echo?sp=hello+world&b=x+y``; ``old=1`` was gone."""
    pressed = _Control("button", name="b", value="x y", kind="submit")
    form = _Form([_Control(name="sp", value="hello world"), pressed],
                 method="get", action="http://h/echo?old=1")
    request = submission(form, pressed)
    assert request == Submission("http://h/echo?sp=hello+world&b=x+y", "GET", None, None)


def test_a_get_submission_with_no_entries_keeps_the_question_mark():
    """Chrome lands on ``/echo?``: the query is replaced, not removed."""
    request = submission(_Form([], method="get", action="http://h/echo?old=1"))
    assert request.url == "http://h/echo?"


def test_a_post_submission_keeps_the_actions_query_and_sends_a_body():
    request = submission(_Form([_Control(name="a", value="1")],
                               action="http://h/echo?keep=1"))
    assert request == Submission("http://h/echo?keep=1", "POST",
                                 b"a=1", URLENCODED)


def test_a_form_in_the_dialog_state_submits_nothing():
    assert submission(_Form([], method="dialog")) is None
