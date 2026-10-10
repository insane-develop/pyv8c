"""``HTMLFormElement``: the controls it owns, its legacy access surface, and the
entry list its submission sends.  ``form[name]``/``form[index]`` are an independent
legacy object in Chromium, so their handlers and ``RadioNodeList`` live here.
"""

from typing import NamedTuple

import pyv8

from ...idl import interface
from ...foundation.arguments import boolean, dom_string
from ..page_errors import LegacyPropertyError, PageTypeError
from .collections import DOMTokenList, HTMLFormControlsCollection, HYPERLINK_REL_TOKENS, NodeList
from .html_elements import Element, HTMLElement, _document_url, _set_url_value, _url_value
from .utils import crlf, encode_entries, replace_query, urlencoded


@interface("RadioNodeList", "NodeList")
class RadioNodeList(NodeList):
    """The live collection returned for duplicate form names."""

        #: A ``NodeList``, so it publishes what one does; stated rather than inherited.

    def get_value(self):
        for item in self._items():
            if getattr(item, "localName", "") == "input" and item.get_type() in ("radio", "checkbox"):
                if item.get_checked():
                    return item.get_value()
        return ""

    def set_value(self, value):
        value = str(dom_string(value))
        for item in self._items():
            if getattr(item, "localName", "") == "input" and item.get_type() == "radio":
                item.set_checked(item.get_value() == value)


#: The tags a form collects; an ``input`` in the image-button state is submittable but not listed.
_LISTED_ELEMENT_TAGS = frozenset((
    "button", "fieldset", "input", "object", "output", "select", "textarea",
))


def _is_listed_element(node):
    """Whether ``node`` is a listed element, by tag and input type."""
    if not isinstance(node, Element) or node.localName not in _LISTED_ELEMENT_TAGS:
        return False
    if node.localName != "input":
        return True
    return str(node.attributes.get("type", "")).strip().lower() != "image"


class Submission(NamedTuple):
    """One form submission as the transport receives it."""

    url: str
    method: str
    body: bytes | None
    content_type: str | None


def submission(form, submitter=None):
    """The request this form's submission makes, or ``None`` for ``method=dialog``."""
    method = form.get_method()
    if method == "dialog":
        return None
    entries = entry_list(form, submitter)
    if method == "get":
        return Submission(replace_query(form.get_action(), urlencoded(entries)),
                          "GET", None, None)
    body, content_type = encode_entries(entries, form.get_enctype())
    return Submission(form.get_action(), "POST", body, content_type)


def entry_list(form, submitter=None):
    """The form's entries as ``(name, value)`` pairs in tree order; *submitter* is
    the pressed button, the only submit button that contributes."""
    entries = []
    for control in form._controls():
        if not _is_successful(control, submitter):
            continue
        entries.extend(_entries_of(control, str(control.attributes.get("name", ""))))
    return entries


def _is_successful(control, submitter):
    """Whether *control* contributes an entry."""
    # Lazy: keep the selector engine off the startup path.
    from .selectors import _is_disabled

    if "name" not in control.attributes or _is_disabled(control):
        return False
    tag = control.localName
    if tag == "button":
        return control.get_type() == "submit" and control is submitter
    if tag == "input":
        kind = control.get_type()
        if kind in ("file", "reset", "button"):
            # file: no picker in this DOM; reset and plain buttons: never sent.
            return False
        if kind in ("checkbox", "radio"):
            return control.get_checked()
        if kind in ("submit", "image"):
            return control is submitter
        return True
    return tag in ("select", "textarea")


def _entries_of(control, name):
    """The entries one successful control contributes."""
    if control.localName == "select" and _is_multiple(control):
        return [(name, option.get_value()) for option in control._options()
                if option.get_selected()]
    return [(name, _value_of(control))]


def _value_of(control):
    """The value a control contributes, with the line endings the wire wants."""
    getter = getattr(control, "get_value", None)
    if getter is None:
        # No live textarea value in this DOM: its raw value is what a page wrote.
        return crlf(control.get_defaultValue())
    return crlf(getter()) if control.localName == "textarea" else str(getter())


def _is_multiple(select):
    """Whether a ``select`` takes one entry per selected option."""
    if "multiple" in select.attributes:
        return True
    size = str(select.attributes.get("size", "0")).strip()
    return size.isdigit() and int(size) > 1


@interface('HTMLFormElement', 'HTMLElement')
class HTMLFormElement(HTMLElement):
    """``<form>``: the controls whose form owner it is.  ``elements`` holds them in
    document order -- descendants and controls elsewhere naming it with ``form=``;
    the owner is the form named by the attribute, else the nearest ancestor form.
    """

    __idl_overrides__ = {
        "interface": {
            "index_del": "__v8_index_del__",
            "index_enum": "__v8_index_enum__",
            "index_get": "__v8_index_get__",
            "index_set": "__v8_index_set__",
            "name_del": "__v8_name_del__",
            "name_enum": "__v8_name_enum__",
            "name_get": "__v8_name_get__",
            "name_set": "__v8_name_set__"
        }
    }
    def get_action(self):
        """The submission URL: the document's own URL, without its fragment, when
        the attribute is absent."""
        return _url_value(self, "action") or _document_url(self).split("#", 1)[0]
    def set_action(self, value): _set_url_value(self, "action", value)

    def get_acceptCharset(self): return self.attributes.get("accept-charset", "")
    def set_acceptCharset(self, value): self.attributes["accept-charset"] = str(dom_string(value))
    def get_autocomplete(self):
        value = str(self.attributes.get("autocomplete", "")).lower()
        return value if value in ("on", "off") else "on"
    def set_autocomplete(self, value):
        value = str(dom_string(value)).lower()
        self.attributes["autocomplete"] = value if value in ("on", "off") else "on"
    def get_enctype(self):
        value = str(self.attributes.get("enctype", "")).lower()
        allowed = ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain")
        return value if value in allowed else allowed[0]
    def set_enctype(self, value):
        value = str(dom_string(value)).lower()
        self.attributes["enctype"] = value
    def get_encoding(self): return self.get_enctype()
    def set_encoding(self, value): self.set_enctype(value)
    def get_method(self):
        value = str(self.attributes.get("method", "")).lower()
        return value if value in ("get", "post", "dialog") else "get"
    def set_method(self, value): self.attributes["method"] = str(dom_string(value)).lower()
    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value): self.attributes["name"] = str(dom_string(value))
    def get_noValidate(self): return "novalidate" in self.attributes
    def set_noValidate(self, value):
        if boolean(value): self.attributes["novalidate"] = ""
        else: self.attributes.pop("novalidate", None)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(dom_string(value))
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", HYPERLINK_REL_TOKENS)
        return value
    def get_target(self): return self.attributes.get("target", "")
    def set_target(self, value): self.attributes["target"] = str(dom_string(value))
    def _owns(self, node):
        """Whether this form is ``node``'s form owner."""
        if "form" in node.attributes:
            form_id = str(node.attributes.get("form", "")).strip()
            lookup = getattr(self.root_node(), "fn_getElementById", None)
            owner = lookup(form_id) if lookup is not None and form_id else None
            return owner is self
        for ancestor in node._ancestors():
            if isinstance(ancestor, HTMLFormElement):
                return ancestor is self
        return False
    def _controls(self):
        return [node for node in self.root_node()._descendants()
                if _is_listed_element(node) and self._owns(node)]
    def get_elements(self):
        value = getattr(self, "_elements_collection", None)
        if value is None:
            value = self._elements_collection = HTMLFormControlsCollection(
                owner=self, getter=self._controls)
        return value
    def get_length(self):
        return len(self._controls())

    def _named_items(self, name):
        value = str(name)
        if not value:
            return []
        return [item for item in self._controls()
                if item.get_id() == value or str(item.attributes.get("name", "")) == value]

    def __v8_name_get__(self, name):
        items = self._named_items(name)
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        view = getattr(self, "_radio_lists", None)
        if view is None:
            view = self._radio_lists = {}
        key = str(name)
        value = view.get(key)
        if value is None:
            value = view[key] = RadioNodeList(owner=self, getter=lambda: self._named_items(key))
        return value

    def __v8_name_set__(self, name, value):
        if self._named_items(name):
            raise LegacyPropertyError.named("set", str(name), "HTMLFormElement", "Named property setter is not supported.")
        return None

    def __v8_name_del__(self, name):
        if self._named_items(name):
            raise LegacyPropertyError.named("delete", str(name), "HTMLFormElement", "Named property deleter is not supported.")
        return None

    def __v8_name_enum__(self):
        return []

    def __v8_index_get__(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        items = self._controls()
        return items[index] if 0 <= index < len(items) else None

    def __v8_index_set__(self, index, value):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self._controls()):
            raise LegacyPropertyError.indexed("set", index, "HTMLFormElement", "Indexed property setter is not supported.")
        return None

    def __v8_index_del__(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self._controls()):
            raise LegacyPropertyError.indexed("delete", index, "HTMLFormElement", "Indexed property deleter is not supported.")
        return None

    def __v8_index_enum__(self):
        return len(self._controls())

    def _validation_controls(self):
        return tuple(control for control in self._controls()
                     if bool(getattr(control, "get_willValidate", lambda: False)()))

    def _check_validity(self, report=False):
        valid = True
        for control in self._validation_controls():
            check = getattr(control, "get_validity", None)
            if check is None or check().get_valid():
                continue
            valid = False
            if report:
                dispatch = getattr(control, "fn_dispatchEvent", None)
                if dispatch is not None:
                    from .events import Event
                    event = Event("invalid", {"bubbles": False, "cancelable": True})
                    event._trusted = True
                    dispatch(event)
        return valid

    def fn_checkValidity(self, *_extra): return self._check_validity(report=True)
    def fn_reportValidity(self, *_extra): return self._check_validity(report=True)

    def fn_requestSubmit(self, submitter=pyv8.Undefined, *_extra):
        if submitter is not pyv8.Undefined and submitter is not pyv8.Null:
            if submitter not in self._controls() or getattr(submitter, "localName", "") not in ("button", "input"):
                raise PageTypeError("The specified value is not a submit button.")
            if getattr(submitter, "localName", "") == "input" and submitter.get_type() not in ("submit", "image"):
                raise PageTypeError("The specified value is not a submit button.")
            if getattr(submitter, "localName", "") == "button" and submitter.get_type() != "submit":
                raise PageTypeError("The specified value is not a submit button.")
        else:
            submitter = None
        self._submit(submitter)
        return pyv8.Undefined

    def _submit(self, submitter=None, *, validate=True, fire_event=True,
                user_activated=False):
        """The submission algorithm: validation, the ``submit`` event, the request.
        A clicked button, ``requestSubmit()`` and ``submit()`` all end here;
        *submitter* joins the entries and *user_activated* becomes ``Sec-Fetch-User``.
        """
        if validate and not self.get_noValidate() and not self._check_validity(report=True):
            return False
        if fire_event:
            from .events import Event
            event = Event("submit", {"bubbles": True, "cancelable": True})
            event._trusted = True
            if not self.fn_dispatchEvent(event):
                return False
        services = self._services
        if services is None:
            return False
        request = submission(self, submitter)
        if request is None:
            return False
        services.document_services.request_navigation(
            request.url, method=request.method, body=request.body,
            content_type=request.content_type, user_activated=user_activated,
        )
        return True

    def fn_submit(self, *_extra):
        """The DOM ``submit()`` method bypasses validation and submit events."""
        self._submit(validate=False, fire_event=False)

    def fn_reset(self, *_extra):
        from .html_input import HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement
        from .events import Event
        event = Event("reset", {"bubbles": True, "cancelable": True})
        event._trusted = True
        if not self.fn_dispatchEvent(event):
            return pyv8.Undefined
        for control in self._controls():
            if isinstance(control, HTMLInputElement):
                control._dirty_value = False
                control._raw_value = ""
                control._dirty_checked = False
                control._checked = "checked" in control.attributes
            elif isinstance(control, HTMLTextAreaElement):
                control._dirty_value = False
            elif isinstance(control, HTMLSelectElement):
                control.set_selectedIndex(control.get_selectedIndex())
        return pyv8.Undefined

__all__ = ["HTMLFormElement", "RadioNodeList"]
