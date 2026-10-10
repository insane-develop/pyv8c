"""The page's mouse: where it is, which buttons are down, and what that fires.

A mouse is a state machine rather than a single "click" call: moving crosses
element boundaries, pressing focuses, and a press and a release on different
elements still make one ``click`` at their nearest common ancestor.  Every
sequence here was observed on the wire from Chrome 153 instead of derived from
the specification:

* a move onto a new element fires ``pointerout``/``pointerover`` and then
  ``mouseout``/``mouseover``, followed by ``pointermove`` and ``mousemove``;
* a press is ``pointerdown``, ``mousedown`` and then focus, in that order --
  and a cancelled ``mousedown`` leaves focus where it was;
* a release is ``pointerup``, ``mouseup`` and then ``click``, whose activation
  behavior runs when no listener cancelled the event; a second click close
  enough in time and place reports ``detail`` 2 and is followed by ``dblclick``;
* a disabled form control receives the pointer events and none of the others,
  which is how the mouse and ``click()`` both treat it;
* ``mouseenter``/``mouseleave`` and their pointer twins do not propagate, so
  only listeners on the element itself ever see them.

The controller owns the position, the button mask and the click counter, and
every entry point runs on the page's owner thread like the rest of the DOM
surface.
"""

from __future__ import annotations

import math
import time

import pyv8

from ..foundation.host_errors import HostStateError
from ..web.dom.activation import accepts_mouse_events, run_activation
from ..web.dom.focus import focus_on_mousedown
from ..web.dom.hit_testing import border_box, element_from_point

#: The DOM's button index and the ``buttons`` mask bit, per button name.
BUTTON_INDEX = {"left": 0, "middle": 1, "right": 2}
BUTTON_BIT = {"left": 1, "middle": 4, "right": 2}

#: The primary mouse pointer, as Chrome describes it on pointer events.
POINTER_ID = 1

#: Chromium's own window for reporting ``detail`` 2 and firing ``dblclick``.
MULTI_CLICK_INTERVAL = 0.5
MULTI_CLICK_DISTANCE = 4.0


def _ancestors(node):
    """*node* and its ancestors, itself first."""
    chain = []
    current = node
    while current is not None:
        chain.append(current)
        current = getattr(current, "parentElement", None)
    return chain


def _leaving(previous, target):
    """The elements a leave event reaches, the element being left first."""
    staying = _ancestors(target) if target is not None else []
    return [node for node in _ancestors(previous)
            if not any(node is other for other in staying)]


def _entering(previous, target):
    """The elements an enter event reaches, the outermost one first."""
    leaving = _ancestors(previous) if previous is not None else []
    chain = [node for node in _ancestors(target)
             if not any(node is other for other in leaving)]
    return list(reversed(chain))


def _common_ancestor(first, second):
    """The nearest element containing both, which is where a press-release clicks."""
    if first is None or second is None:
        return first or second
    below = _ancestors(second)
    for node in _ancestors(first):
        if any(node is other for other in below):
            return node
    return None


def _pixel(value):
    """Chrome's rounding for a mouse event's coordinate: halves away from zero."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def describe(element):
    """A short ``<tag id="...">`` label for error messages."""
    if element is None:
        return "nothing"
    tag = getattr(element, "localName", None) or "node"
    identifier = getattr(element, "attributes", {}).get("id")
    return f"<{tag} id={identifier!r}>" if identifier else f"<{tag}>"


#: Positions a human path is sampled at when the caller names no count.
HUMAN_SAMPLES = 24


def _straight_points(start, end, samples):
    """*samples* positions evenly spaced from *start* to *end*, the last one at *end*."""
    start_x, start_y = start
    end_x, end_y = end
    return [(start_x + (end_x - start_x) * step / samples,
             start_y + (end_y - start_y) * step / samples) for step in range(1, samples + 1)]


def _curve_points(curve, start, end, samples, seed):
    """The positions a hand passes through, ending exactly at *end*.

    A real reach bows sideways and starts and stops slowly.  The same *seed* replays
    the same path, which is what keeps a test that asks for one reproducible.
    """
    if curve != "human":
        raise ValueError(f"unknown curve {curve!r}; expected 'human'")
    import random

    start_x, start_y = start
    end_x, end_y = end
    delta_x, delta_y = end_x - start_x, end_y - start_y
    reach = math.hypot(delta_x, delta_y)
    samples = max(2, int(samples or HUMAN_SAMPLES))
    if not reach:
        return [(end_x, end_y)] * samples
    rng = random.Random(seed)
    # The control point sits off the straight line: that offset is the bow.
    bow = rng.uniform(-0.15, 0.15) * reach
    control_x = start_x + delta_x / 2 - delta_y / reach * bow
    control_y = start_y + delta_y / 2 + delta_x / reach * bow
    wobble = min(1.0, reach * 0.01)
    points = []
    for index in range(1, samples + 1):
        eased = 0.5 - 0.5 * math.cos(math.pi * index / samples)
        inverse = 1.0 - eased
        point_x = (inverse * inverse * start_x + 2 * inverse * eased * control_x
                   + eased * eased * end_x)
        point_y = (inverse * inverse * start_y + 2 * inverse * eased * control_y
                   + eased * eased * end_y)
        if index < samples:
            # Every position but the landing one wanders, the way a hand's does.
            point_x += rng.uniform(-wobble, wobble)
            point_y += rng.uniform(-wobble, wobble)
        points.append((point_x, point_y))
    return points


class MouseInput:
    """One page's mouse, reached as ``page.mouse``.

    ``position`` and ``pressed`` are plain reads of host-side state and may be
    read from any thread; everything that dispatches events marshals onto the
    page's owner thread, so it may be called from any thread too.
    """

    def __init__(self, page):
        self._page = page
        self._x = 0.0
        self._y = 0.0
        self._movement_x = 0
        self._movement_y = 0
        self._moved = False
        self._bits = 0
        self._hovered = None
        #: The pointer starts outside the page, so its first move inside reports the
        #: document picking it up, as a window does when a cursor arrives.
        self._inside_page = False
        self._pressed = None
        self._press_count = 1
        self._count = 0
        self._last_click = None

    @property
    def position(self) -> tuple[float, float]:
        """The mouse position as ``(x, y)`` in viewport coordinates."""
        return (self._x, self._y)

    @property
    def pressed(self) -> frozenset[str]:
        """The buttons currently held down, as a set of names."""
        return frozenset(name for name, bit in BUTTON_BIT.items() if self._bits & bit)

    def move(self, x: float, y: float, *, steps: int | None = None,
             duration: float | None = None, curve: str | None = None,
             seed: int | None = None) -> None:
        """Move the mouse to ``(x, y)``, moving through *steps* positions.

        Each position fires the boundary events a crossing element makes and
        then the two move events, so a page watching ``mouseover`` sees the
        same sequence a real cursor produces.

        :param x: viewport x coordinate.
        :param y: viewport y coordinate.
        :param steps: how many positions to report; ``1`` jumps, and a curve
            samples :data:`HUMAN_SAMPLES` of its own when this is left out.
        :param duration: seconds to spread the positions over; without it they
            are dispatched as fast as they are built, which is what a
            scripted driver usually wants.
        :param curve: ``"human"`` walks a bowed, eased path instead of the
            straight line, for a page that reads the shape of the movement.
        :param seed: makes a curve replayable; without it each call bows its own way.
        """
        return self._call(self._move_to, float(x), float(y), steps, duration=duration,
                          curve=curve, seed=seed)

    def down(self, *, button: str = "left", click_count: int | None = None) -> None:
        """Press *button* where the mouse is, and focus what the press lands on.

        :param button: ``"left"``, ``"middle"`` or ``"right"``.
        :param click_count: the ``detail`` both the press and the matching click
            report; by default a press close to the last one continues its run.
        """
        return self._call(self._down, self._button(button), click_count)

    def up(self, *, button: str = "left", click_count: int | None = None) -> None:
        """Release *button*, firing the click a completed press produces.

        The click lands on the nearest common ancestor of the press and the
        release, which is the element itself when both happened in one place.
        """
        return self._call(self._up, self._button(button), click_count)

    def click(self,
              x: float | None = None,
              y: float | None = None,
              *,
              button: str = "left",
              click_count: int | None = None,
              delay: float | None = None) -> None:
        """Move to ``(x, y)`` when both are given, then press and release *button*.

        :param delay: seconds the button stays down before it is released; a real
            press lasts tens of milliseconds, a scripted one none at all.
        """
        return self._call(self._click, x, y, self._button(button), click_count, delay=delay)

    def dblclick(self,
                 x: float | None = None,
                 y: float | None = None,
                 *,
                 button: str = "left",
                 delay: float | None = None) -> None:
        """Two clicks reported as one run, ending in ``dblclick`` on the second."""
        return self._call(self._dblclick, x, y, self._button(button), delay=delay)

    def move_to_element(self, element, *, force: bool = False,
                        duration: float | None = None, curve: str | None = None,
                        seed: int | None = None) -> None:
        """Move the mouse to the centre of *element*, as :meth:`move` does.

        :raises HostStateError: the element has no box (it is not rendered),
            or something else covers the centre and *force* is false.
        """
        return self._call(self._move_to_element, element, force, duration=duration,
                          curve=curve, seed=seed)

    def click_element(self, element, *, button: str = "left", click_count: int = 1,
                      force: bool = False, delay: float | None = None,
                      duration: float | None = None) -> None:
        """Move to the centre of *element* and click there, the way a user would.

        :raises HostStateError: the element has no box, or the centre is not
            the element or one of its descendants and *force* is false.
        """
        return self._call(
            self._click_element, element, self._button(button), int(click_count), force,
            delay=delay, duration=duration,
        )

    def _call(self, action, *args, **kwargs):
        return self._page.realm.executor.call(action, *args, **kwargs)

    def _button(self, name):
        if name not in BUTTON_INDEX:
            raise ValueError(
                f"unknown mouse button {name!r}; expected one of "
                f"{', '.join(sorted(BUTTON_INDEX))}"
            )
        return name

    def _hit(self, x, y):
        """``(hit, target)`` for a point: what is under it, and what an event lands on."""
        document = self._page.document
        hit = element_from_point(document, x, y)
        if hit is not None:
            return hit, hit
        root = document.get_documentElement()
        return None, None if root is None or root is pyv8.Null else root

    def _target(self):
        """The element the mouse is over, or the root when it is outside the viewport."""
        return self._hit(self._x, self._y)[1]

    def _emit(self, event_type, target, *, button=0, detail=0, related=None,
              bubbles=True, cancelable=True, event_class=None, propagates=True,
              coalesced=()):
        """Dispatch one event and hand it back, or ``None`` when there is no target."""
        if target is None:
            return None
        options = {
            #: A pointer or mouse event from real input crosses shadow boundaries in Chrome,
            #: which a driven-input probe measured (``composed`` true for every one of them).
            "composed": True,
            "bubbles": bubbles, "cancelable": cancelable, "detail": detail,
            "button": button, "buttons": self._bits,
            "clientX": self._x, "clientY": self._y,
            # Nothing scrolls and no window position is modelled, so the screen pair is
            # the viewport pair, as it is for a window at the screen's own corner.
            "screenX": self._x, "screenY": self._y,
            "movementX": self._movement_x if event_type.endswith("move") else 0,
            "movementY": self._movement_y if event_type.endswith("move") else 0,
        }
        if related is not None:
            options["relatedTarget"] = related
        interface = event_class or ("PointerEvent" if event_type.startswith("pointer") else "MouseEvent")
        if interface == "PointerEvent":
            options.update({"pointerId": POINTER_ID, "pointerType": "mouse", "isPrimary": True})
            if coalesced:
                options["coalesced"] = tuple(coalesced)
            if not event_type.endswith(("down", "up")):
                # No button transition is -1 on a pointer event; the mouse event
                # beside it keeps the 0 Chromium gives it.
                options["button"] = -1
        else:
            # A mouse event reports whole pixels; the pointer event beside it keeps
            # the fraction the layout measured.
            for name in ("clientX", "clientY", "screenX", "screenY"):
                options[name] = _pixel(options[name])
        return self._page.lifecycle.dispatch_page_event(
            event_type, target, options, event_class=interface, propagates=propagates,
        )

    def _move_to(self, x, y, steps, known=None, duration=None, curve=None, seed=None):
        """Move through *steps* positions, evenly spaced, or along *curve* when one is named."""
        start, end = (self._x, self._y), (float(x), float(y))
        # A caller that already read the landing point hands it in for the last step.
        points = (_curve_points(curve, start, end, steps, seed) if curve is not None
                  else _straight_points(start, end, max(1, int(steps or 1))))
        slice_seconds = 0.0 if duration is None else max(0.0, float(duration)) / len(points)
        batch = []
        for index, (point_x, point_y) in enumerate(points, start=1):
            if slice_seconds:
                # The document's clock follows real time, so the pause is what makes the
                # positions arrive apart, the way a cursor's do.
                time.sleep(slice_seconds)
                # A paced position is its own reading: nothing is merged into it.
                batch = []
            pointer = self._move_once(point_x, point_y, known if index == len(points) else None,
                                      tuple(batch))
            if pointer is not None:
                # A burst arrives as one batch; a page can read what was merged into it.
                batch.append(pointer)

    def _move_once(self, x, y, known=None, coalesced=()):
        # Chrome reports whole pixels, and the first move after the cursor arrives
        # reports no movement at all.
        if self._moved:
            self._movement_x, self._movement_y = _pixel(x - self._x), _pixel(y - self._y)
        else:
            self._movement_x = self._movement_y = 0
            self._moved = True
        self._x, self._y = x, y
        # One hit test per position: it walks the whole tree, so both answers come from it.
        hit, target = self._hit(x, y) if known is None else known
        self._cross(target, hit is not None)
        pointer = self._emit("pointermove", target, coalesced=coalesced)
        self._emit("mousemove", target)
        return pointer

    def _cross(self, target, inside=True):
        """Fire the boundary events a move from the hovered element to *target* makes."""
        previous = self._hovered
        page_changed = inside != self._inside_page
        if target is previous and not page_changed:
            return
        self._inside_page = inside
        self._hovered = target
        for kind, interface in (("pointer", "PointerEvent"), ("mouse", "MouseEvent")):
            if previous is not None and target is not previous:
                self._emit(f"{kind}out", previous, related=target, event_class=interface)
                for node in _leaving(previous, target):
                    self._emit(f"{kind}leave", node, related=target, event_class=interface,
                               bubbles=False, cancelable=False, propagates=False)
            if target is not None and target is not previous:
                self._emit(f"{kind}over", target, related=previous, event_class=interface)
            # The document reports the cursor arriving in and leaving the page: its
            # enter is the outermost one, and its leave the last.
            if page_changed and inside:
                self._page_event(kind, interface, "enter")
            if target is not None and target is not previous:
                for node in _entering(previous, target):
                    self._emit(f"{kind}enter", node, related=previous, event_class=interface,
                               bubbles=False, cancelable=False, propagates=False)
            if page_changed and not inside:
                self._page_event(kind, interface, "leave")

    def _page_event(self, kind, interface, phase):
        self._emit(f"{kind}{phase}", self._page.document, event_class=interface,
                   bubbles=False, cancelable=False, propagates=False)

    def _count_for(self, target, click_count):
        """The ``detail`` this press reports, and the run it belongs to."""
        if click_count is not None:
            self._count = max(1, int(click_count))
        else:
            now = time.monotonic()
            previous = self._last_click
            if (previous is not None
                    and now - previous[0] <= MULTI_CLICK_INTERVAL
                    and abs(self._x - previous[1]) <= MULTI_CLICK_DISTANCE
                    and abs(self._y - previous[2]) <= MULTI_CLICK_DISTANCE
                    and previous[3] is target):
                self._count += 1
            else:
                self._count = 1
        self._last_click = (time.monotonic(), self._x, self._y, target)
        return self._count

    def _down(self, button, click_count):
        target = self._target()
        count = self._count_for(target, click_count)
        self._bits |= BUTTON_BIT[button]
        self._pressed, self._press_count = target, count
        self._emit("pointerdown", target, button=BUTTON_INDEX[button])
        if not accepts_mouse_events(target):
            return
        event = self._emit("mousedown", target, button=BUTTON_INDEX[button], detail=count)
        # Focus is all a cancelled press takes away; the click still fires.
        if event is None or not event.defaultPrevented:
            focus_on_mousedown(target)

    def _up(self, button, click_count):
        target = self._target()
        count = self._press_count if click_count is None else max(1, int(click_count))
        pressed = self._pressed
        self._pressed = None
        self._bits &= ~BUTTON_BIT[button]
        self._emit("pointerup", target, button=BUTTON_INDEX[button])
        if not accepts_mouse_events(target):
            return
        self._emit("mouseup", target, button=BUTTON_INDEX[button], detail=count)
        clicked = _common_ancestor(pressed, target) or target
        if button != "left":
            self._emit("auxclick", clicked, button=BUTTON_INDEX[button], detail=count)
            if button == "right":
                self._emit("contextmenu", clicked, button=BUTTON_INDEX[button])
            return
        event = self._emit("click", clicked, detail=count)
        if event is not None and not event.defaultPrevented:
            run_activation(clicked, event)
        if count == 2:
            self._emit("dblclick", clicked, detail=count)

    def _click(self, x, y, button, click_count, known=None, delay=None, duration=None):
        if x is not None and y is not None:
            self._move_to(float(x), float(y), 1, known, duration)
        self._down(button, click_count)
        if delay:
            time.sleep(max(0.0, float(delay)))
        self._up(button, click_count)

    def _dblclick(self, x, y, button, delay=None):
        self._click(x, y, button, 1, None, delay)
        self._click(None, None, button, 2, None, delay)

    def _point_in(self, element, force, action):
        """The centre of *element*, once it is the element a click would reach there."""
        box = border_box(element)
        if box.width <= 0 or box.height <= 0:
            raise HostStateError(
                f"{describe(element)} has no box to click: it is not rendered"
            )
        x, y = box.left + box.width / 2, box.top + box.height / 2
        # Nothing runs between this read and the action, so the landing the check saw is
        # the one the action starts from.
        known = None if force else self._hit(x, y)
        if known is not None:
            hit = known[0]
            covered = hit is not element and not any(
                node is element for node in _ancestors(hit)
            )
            if covered:
                raise HostStateError(
                    f"{describe(element)} is covered at its centre ({x:g}, {y:g})"
                    f" by {describe(hit)}; pass force=True to click there anyway"
                )
        return action(x, y, known)

    def _move_to_element(self, element, force, duration=None, curve=None, seed=None):
        return self._point_in(
            element, force,
            lambda x, y, known: self._move_to(x, y, None, known, duration, curve, seed),
        )

    def _click_element(self, element, button, click_count, force, delay=None, duration=None):
        return self._point_in(
            element, force,
            lambda x, y, known: self._click(x, y, button, click_count, known, delay, duration),
        )


__all__ = ["MouseInput"]
