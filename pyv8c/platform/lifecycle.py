"""Document lifecycle events and ready-state transitions for a Page."""

import pyv8


from ..foundation.host_errors import HostStateError
from ..foundation.error_policy import is_host_failure
from ..foundation.logging import logger
from ..web.bom.utils import page_secure_context
from ..web.dom.events import Event, MOTION_EVENT_TYPES

#: What Chrome reports for the interval of a reading with no sensor behind it.
DEVICE_INTERVAL_MS = 16

#: Event types the host builds as a richer class than ``Event``; others fall back to ``Event``.
EVENT_CLASS_BY_TYPE = {
    "click": "MouseEvent", "dblclick": "MouseEvent", "auxclick": "MouseEvent",
    "contextmenu": "MouseEvent", "mousedown": "MouseEvent", "mouseup": "MouseEvent",
    "mousemove": "MouseEvent", "mouseover": "MouseEvent", "mouseout": "MouseEvent",
    "mouseenter": "MouseEvent", "mouseleave": "MouseEvent",
    "focus": "FocusEvent", "blur": "FocusEvent", "focusin": "FocusEvent", "focusout": "FocusEvent",
    "input": "InputEvent", "beforeinput": "InputEvent",
    "keydown": "KeyboardEvent", "keyup": "KeyboardEvent", "keypress": "KeyboardEvent",
    "pointerdown": "PointerEvent", "pointerup": "PointerEvent", "pointermove": "PointerEvent",
    "pointerover": "PointerEvent", "pointerout": "PointerEvent", "pointerenter": "PointerEvent",
    "pointerleave": "PointerEvent", "pointercancel": "PointerEvent",
    "gotpointercapture": "PointerEvent", "lostpointercapture": "PointerEvent",
    "wheel": "WheelEvent",
    "compositionstart": "CompositionEvent", "compositionupdate": "CompositionEvent",
    "compositionend": "CompositionEvent",
    "popstate": "PopStateEvent", "hashchange": "HashChangeEvent", "storage": "StorageEvent",
    "message": "MessageEvent", "messageerror": "MessageEvent",
    "unhandledrejection": "PromiseRejectionEvent", "rejectionhandled": "PromiseRejectionEvent",
    "pageshow": "PageTransitionEvent", "pagehide": "PageTransitionEvent",
    "beforeunload": "BeforeUnloadEvent",
    "pagereveal": "PageRevealEvent", "pageswap": "PageSwapEvent",
    "animationstart": "AnimationEvent", "animationend": "AnimationEvent",
    "animationiteration": "AnimationEvent", "animationcancel": "AnimationEvent",
    "transitionrun": "TransitionEvent", "transitionstart": "TransitionEvent",
    "transitionend": "TransitionEvent", "transitioncancel": "TransitionEvent",
    "error": "ErrorEvent", "progress": "ProgressEvent",
    "loadstart": "ProgressEvent", "loadend": "ProgressEvent",
    "devicemotion": "DeviceMotionEvent",
    "deviceorientation": "DeviceOrientationEvent",
    "deviceorientationabsolute": "DeviceOrientationEvent",
}

#: Host-dispatched types that bubble by default, mirroring Blink's defaults.
BUBBLING_EVENT_TYPES = frozenset((
    "click", "dblclick", "auxclick", "contextmenu",
    "mousedown", "mouseup", "mousemove", "mouseover", "mouseout",
    "keydown", "keyup", "keypress",
    "input", "beforeinput", "change", "select", "invalid", "submit", "reset",
    "wheel", "pointerdown", "pointerup", "pointermove", "pointerover", "pointerout",
    "pointercancel", "gotpointercapture", "lostpointercapture",
    "touchstart", "touchend", "touchmove", "touchcancel",
    "compositionstart", "compositionupdate", "compositionend",
    "animationstart", "animationend", "animationiteration", "animationcancel",
    "transitionrun", "transitionstart", "transitionend", "transitioncancel",
    "visibilitychange",
))

#: Host-dispatched types that are cancelable by default.
CANCELABLE_EVENT_TYPES = frozenset((
    "click", "dblclick", "auxclick", "contextmenu", "mousedown", "mouseup",
    "keydown", "keypress", "beforeinput", "submit", "reset", "wheel",
    "pointerdown", "pointerup", "touchstart", "touchend", "touchmove",
    "beforeunload",
))


class DocumentLifecycle:
    """Own document lifecycle state; runs on the owning Page thread."""

    def __init__(self, page):
        self.page = page
        self.dom_content_loaded = False
        self.document_load_fired = False
        self.load_waiters = []
        self._event_classes = {}
        self._event_session = None

    def materialize_event_class(self, event_class):
        """Resolve ``event_class`` (an interface name or a class) for this realm.

        The native side needs a committed class config before a Python-built event
        can become a JS object; a second call is a no-op.  One document loading two
        hundred images resolves the same class for every one of them, so the answer
        is remembered until a navigation replaces the realm.
        """
        session = self.page.binding_session
        if session is not self._event_session:
            self._event_classes = {}
            self._event_session = session
        cached = self._event_classes.get(event_class)
        if cached is not None:
            return cached
        name = event_class if isinstance(event_class, str) else getattr(
            getattr(event_class, "__idl_spec__", None), "name", None
        )
        if name is None:
            return event_class
        if name not in session.interface_names("Window"):
            raise TypeError(f"unknown event interface {name!r}")
        cls = session.materialize(name)
        if not issubclass(cls, Event):
            raise TypeError(f"{name!r} is not an Event interface")
        self._event_classes[event_class] = cls
        return cls

    def event_class(self, event_type, event_class=None):
        """The implementation class used to build one dispatched event.

        When ``event_class`` is omitted, the type picks a default from
        :data:`EVENT_CLASS_BY_TYPE`.
        """
        return self.materialize_event_class(
            event_class or EVENT_CLASS_BY_TYPE.get(str(event_type), "Event")
        )

    def dispatch_page_event(self, event_type, target, options=None, *, event_class=None,
                            propagates=True):
        """Dispatch one trusted event at *target* and return it.

        *propagates* is ``False`` for the enter/leave pair: Chromium dispatches
        ``mouseenter`` and relatives at the target alone.
        """
        cls = Event if event_class is None else self.materialize_event_class(event_class)
        event = cls(event_type, options or {})
        event._trusted = True
        event._non_propagating = not propagates
        if event_type in ("pagehide", "pageshow"):
            event._persisted = bool((options or {}).get("persisted", False))
        target.fn_dispatchEvent(event)
        return event

    def dispatch_event(self, event_type, *, target="window", selector=None, event_class=None, init=None):
        """Build a trusted event of the type's default class and dispatch it.

        Returns ``True`` when the event was not cancelled.
        """
        node = self.event_target(target, selector)
        data = dict(init or {})
        data.setdefault("bubbles", str(event_type) in BUBBLING_EVENT_TYPES)
        data.setdefault("cancelable", str(event_type) in CANCELABLE_EVENT_TYPES)
        event = self.dispatch_page_event(
            event_type, node, data, event_class=self.event_class(event_type, event_class),
        )
        return not event.defaultPrevented

    def event_target(self, target="window", selector=None):
        """Resolve a host-side dispatch target to a live EventTarget."""
        page = self.page
        if selector is not None:
            node = page.document.fn_querySelector(str(selector))
            if node is pyv8.Null or node is None:
                raise HostStateError(f"No element matches the selector {selector!r}")
            return node
        if target == "window" or target is page.window:
            return page.window
        if target == "document" or target is page.document:
            return page.document
        if isinstance(target, str):
            raise ValueError("target must be 'window', 'document' or a DOM node")
        if not callable(getattr(target, "fn_dispatchEvent", None)):
            raise TypeError("target must be an EventTarget")
        return target

    def dispatch_beforeunload(self):
        event = self.dispatch_page_event(
            "beforeunload", self.page.window,
            {"bubbles": False, "cancelable": True}, event_class="BeforeUnloadEvent",
        )
        return not event.defaultPrevented and not event.get_returnValue()

    def dispatch_page_show(self, persisted=False):
        # Chrome reports pageshow as a bubbling event, though it is fired at the window.
        return self.dispatch_page_event(
            "pageshow", self.page.window, {"bubbles": True, "persisted": bool(persisted)},
            event_class="PageTransitionEvent",
        )

    def dispatch_page_reveal(self):
        """The one ``pagereveal`` a document gets, as Chrome sends it after load."""
        return self.dispatch_page_event("pagereveal", self.page.window, {"bubbles": False},
                                        event_class="PageRevealEvent")

    def dispatch_pageswap(self):
        """``pageswap``: what Chrome sends to the document being replaced."""
        return self.dispatch_page_event("pageswap", self.page.window, {"bubbles": False},
                                        event_class="PageSwapEvent")

    def dispatch_visibility_hidden(self):
        """A navigation hides the document, which Chrome reports before unload."""
        page = self.page
        if page.document.hidden:
            return None
        page.document.hidden = True
        return self.dispatch_page_event("visibilitychange", page.document, {"bubbles": True})

    def dispatch_page_hide(self, persisted=False):
        return self.dispatch_page_event(
            "pagehide", self.page.window, {"bubbles": False, "persisted": bool(persisted)},
            event_class="PageTransitionEvent",
        )

    def dispatch_unload(self):
        return self.dispatch_page_event("unload", self.page.window, {"bubbles": False})

    def dispatch_progress_event(self, target, event_type, loaded=0, total=0, length_computable=False):
        """Fire one element ``loadstart``/``progress``/``loadend`` event."""
        return self.dispatch_page_event(
            event_type, target,
            {
                "bubbles": False,
                "lengthComputable": bool(length_computable),
                "loaded": int(loaded),
                "total": int(total),
            },
            event_class="ProgressEvent",
        )

    def dispatch_script_error(self, node, error, *, message=None, filename="", line=0,
                              column=0, value=None):
        """Fire ``error`` for *error*, or for the pieces V8 reported directly.

        An exception raised inside a microtask job never returns to Python, so
        the isolate reports its message, position and thrown value instead.

        A failure outside the error family is the host's own -- a script that could
        not be fetched, a handler the host could not build.  What a page is told for
        one is the ``error`` a browser reports on the element; the reason stays in the
        log and never becomes the event's message or its ``error`` value.
        """
        if node is not None:
            self.dispatch_page_event("error", node, {"bubbles": False})
        if message is None:
            # A browser hands handlers the original JavaScript error, so a binding
            # wrapper is replaced by the JS value it stands for.
            original = getattr(error, "original", None)
            if error is not None and is_host_failure(error):
                #: The reason is the host's own -- a load that failed, a handler it could
                #: not build -- so the page is told the script did not arrive, with no
                #: host text and no exception object behind it; the reason goes to the log.
                logger.warning("a script's failure was the host's own: {}", error)
                message = "Failed to load the script"
            else:
                message = str(error)
                value = error if original is None else original
        event = self.materialize_event_class("ErrorEvent")(
            "error", {
                "message": message,
                "filename": filename,
                "lineno": line,
                # V8 counts a column from zero; an ErrorEvent counts from one.
                "colno": column + 1 if line else 0,
                "error": value,
            },
        )
        event._trusted = True
        self.page.window.fn_dispatchEvent(event)
        return event

    def dispatch_rejection(self, event_type, promise, reason):
        """Fire ``unhandledrejection`` or ``rejectionhandled`` on the window.

        The environment cannot see the handlers attached to a rejected promise,
        so the event is built from what V8 named; a ``rejectionhandled`` carries
        no reason.
        """
        event = self.materialize_event_class(EVENT_CLASS_BY_TYPE[event_type])(
            event_type, {"promise": promise, "reason": reason},
        )
        event._trusted = True
        self.page.window.fn_dispatchEvent(event)
        return event

    def set_visibility(self, hidden):
        """Flip the document visibility state and fire the lifecycle events.

        A redundant call is a no-op, matching the browser's transition-only contract.
        """
        page = self.page
        hidden = bool(hidden)
        if bool(page.document.hidden) == hidden:
            return False
        page.document.hidden = hidden
        self.dispatch_page_event("visibilitychange", page.document, {"bubbles": True})
        self.dispatch_page_event("freeze" if hidden else "resume", page.document, {"bubbles": False})
        return True

    def set_ready_state(self, value):
        if self.page.document.ready_state == value:
            return
        self.page.document.ready_state = value
        self.dispatch_page_event("readystatechange", self.page.document, {"bubbles": False})

    def deliver_motion_events(self, event_type=None):
        """Deliver one motion reading for each type the window listens for.

        Chrome starts as soon as a page listens: the first reading arrives with the
        document loaded, and a listener added later is handed one on the next task.
        There is no sensor here, so the reading is the empty one Chrome sends -- every
        field null and the interval it reports -- which is what a page reads it for.
        """
        page = self.page
        window = page.window
        if not page_secure_context(page):
            return
        for name in MOTION_EVENT_TYPES if event_type is None else (event_type,):
            if not window._listeners.get(name):
                continue
            init = ({"interval": DEVICE_INTERVAL_MS} if name == "devicemotion"
                    else {"absolute": name == "deviceorientationabsolute"})
            self.dispatch_page_event(name, window, {"bubbles": False, **init},
                                     event_class=self.event_class(name))

    def motion_listener_added(self, event_type):
        """A motion listener appeared on the window: hand it the reading it waits for.

        Before the document has loaded, that is the load-time delivery; after it,
        Chrome delivers one straight away, so this one is queued.
        """
        if event_type not in MOTION_EVENT_TYPES or not self.document_load_fired:
            return
        self.page.tasks.post(lambda: self.deliver_motion_events(event_type))

    def maybe_fire_dom_content_loaded(self):
        page = self.page
        if (
            page.closed
            or not page.navigation.parser_done
            or self.dom_content_loaded
            or page.scripts.dom_blocking_scripts
        ):
            return
        self.dom_content_loaded = True
        self.set_ready_state("interactive")
        performance = page.services.realm_services.performance
        clock = performance.clock
        performance.note(domInteractive=clock.now_ms(), domContentLoadedEventStart=clock.now_ms())
        self.dispatch_page_event("DOMContentLoaded", page.document, {"bubbles": True})
        performance.note(domContentLoadedEventEnd=clock.now_ms())
        self.maybe_fire_document_load()

    def maybe_fire_document_load(self):
        page = self.page
        if page.closed or not self.dom_content_loaded or page.resources.resources or self.document_load_fired:
            return
        self.document_load_fired = True
        self.set_ready_state("complete")
        performance = page.services.realm_services.performance
        clock = performance.clock
        performance.note(domComplete=clock.now_ms(), loadEventStart=clock.now_ms())
        # A window's load event is fired at the window with the document as its target.
        event = self.event_class("load")("load", {"bubbles": False})
        event._trusted = True
        event.target = page.document
        page.window.fn_dispatchEvent(event)
        performance.note(loadEventEnd=clock.now_ms())
        self.dispatch_page_show(False)
        self.dispatch_page_reveal()
        self.deliver_motion_events()
        waiters, self.load_waiters = self.load_waiters, []
        for callback in waiters:
            callback(True)

    def when_loaded(self, callback):
        if self.document_load_fired:
            self.page.tasks.post(lambda: callback(True))
        else:
            self.load_waiters.append(callback)
