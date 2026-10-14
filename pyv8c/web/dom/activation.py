"""What a click does to an element besides dispatching the event.

The behavior belongs to the nearest ancestor that has one and runs only when
the ``click`` is not cancelled; a disabled form control is inert.  Behaviour
measured against Chrome 153.
"""

import pyv8


#: Local names that a ``disabled`` attribute makes inert.
_FORM_CONTROLS = frozenset((
    "button", "fieldset", "input", "optgroup", "option", "select", "textarea",
))


def is_disabled_form_control(element):
    """Whether *element* is a form control switched off by its own attribute."""
    return (
        getattr(element, "localName", "") in _FORM_CONTROLS
        and "disabled" in getattr(element, "attributes", {})
    )


def user_activated(event):
    """Whether *event* is a user gesture: a trusted click is, a page's own
    ``element.click()`` is not (measured on Chrome 153)."""
    return bool(getattr(event, "_trusted", False))


def accepts_mouse_events(element):
    """Whether the mouse reaches *element*: a disabled control still gets pointer
    events but neither mouse events nor focus."""
    return element is not None and element is not pyv8.Null and not is_disabled_form_control(element)


def activation_target(node):
    """The nearest element at or above *node* whose click runs a behavior."""
    candidate = node
    while candidate is not None and candidate is not pyv8.Null:
        activatable = getattr(candidate, "_activatable", None)
        if activatable is not None and activatable():
            return candidate
        candidate = getattr(candidate, "parentElement", None)
    return None


def run_activation(node, event=None):
    """Run the activation behavior a click on *node* reaches; reports whether one
    ran.  *event* is the click that got here, which a behavior may pass on."""
    target = activation_target(node)
    if target is None:
        return False
    target._run_activation(event)
    return True


def prepare_activation(node):
    """Checkbox legacy pre-activation: listeners see the new checked state."""
    target = activation_target(node)
    if (target is None or getattr(target, "localName", "") != "input"
            or target.get_type() != "checkbox" or is_disabled_form_control(target)):
        return None
    before = (target, target._checked, target._indeterminate)
    target._dirty_checked = True
    target._checked = not target._checked
    target._indeterminate = False
    return before


def complete_activation(node, event, prepared):
    """Cancellation restores checkedness and indeterminate, while remaining dirty."""
    if event is None or event.defaultPrevented:
        if prepared is not None:
            target, checked, indeterminate = prepared
            target._checked, target._indeterminate = checked, indeterminate
        return False
    if prepared is not None:
        event._activation_prepared = prepared[0]
    return run_activation(node, event)


def click_element(element):
    """The activation steps ``element.click()`` runs: an untrusted ``click`` with
    ``detail`` 0, then the behavior unless it was cancelled."""
    if not accepts_mouse_events(element):
        return False
    from .events import PointerEvent
    #: A click a script makes is not a user's, so Chrome reports ``isTrusted`` false here --
    #: the one event the engine raises that stays untrusted.
    event = PointerEvent("click", {"composed": True,
        "bubbles": True, "cancelable": True, "detail": 0,
        "button": 0, "buttons": 0, "clientX": 0, "clientY": 0,
        "pointerId": -1, "pointerType": "", "isPrimary": False, "pressure": 0,
    })
    prepared = prepare_activation(element)
    element.fn_dispatchEvent(event)
    complete_activation(element, event, prepared)
    return True


def repeat_click(element, source):
    """Run a click on *element* that repeats *source* -- same coordinates, click
    count and trust -- as a label forwards its press to the control."""
    from .events import PointerEvent
    repeated = PointerEvent("click", {"composed": True,
        "isTrusted": getattr(source, "isTrusted", False),
        "bubbles": True, "cancelable": True,
        "detail": getattr(source, "detail", 0),
        "button": getattr(source, "button", 0),
        "buttons": 0,
        "clientX": getattr(source, "clientX", 0),
        "clientY": getattr(source, "clientY", 0),
        "screenX": getattr(source, "screenX", 0),
        "screenY": getattr(source, "screenY", 0),
        "pointerId": getattr(source, "pointerId", -1),
        "pointerType": getattr(source, "pointerType", ""),
        "isPrimary": getattr(source, "isPrimary", False),
        "pressure": getattr(source, "pressure", 0),
        "width": getattr(source, "width", 1), "height": getattr(source, "height", 1),
    })
    repeated._trusted = bool(getattr(source, "_trusted", False))
    prepared = prepare_activation(element)
    element.fn_dispatchEvent(repeated)
    complete_activation(element, repeated, prepared)
    return True

__all__ = [
    "accepts_mouse_events", "activation_target", "click_element",
    "is_disabled_form_control", "repeat_click", "run_activation", "user_activated",
    "prepare_activation", "complete_activation",
]
