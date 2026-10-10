"""The timer and microtask surface every window-like global exposes.

``Window`` and ``WorkerGlobalScope`` share these algorithms, so the code lives
here and each global mixes it in; the IDL declarations stay with the interface
that exposes them.
"""

import pyv8

from ...foundation.arguments import (
    MISSING,
    callback_value,
    dom_string,
    integer,
    missing_error,
)
from ...foundation.clock import clock_of
from ...foundation.logging import logger


#: A frame falls due one display refresh after the last, as a compositor paces it.
ANIMATION_FRAME_MS = 1000.0 / 60.0


class _SchedulingMixin:
    """``setTimeout``/``setInterval``/``queueMicrotask`` for one global scope.

    Mixing this in requires ``task_services`` (timer queue) and ``realm_services``
    (scripting and microtask queue).
    """

    #: IdlInterface name the missing-argument errors report.
    _scheduling_interface = "Window"

    def fn_setTimeout(self, callback=MISSING, delay=0, *arguments):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'setTimeout', 1, 0, constructor=False)
        return self._schedule(callback, delay, arguments, repeat=False)

    def fn_setInterval(self, callback=MISSING, delay=0, *arguments):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'setInterval', 1, 0, constructor=False)
        return self._schedule(callback, delay, arguments, repeat=True)

    def _schedule(self, callback, delay, arguments, *, repeat):
        """Queue one timer, accepting the string form HTML also allows."""
        name = "setInterval" if repeat else "setTimeout"
        source = None
        if not callable(callback):
            source = dom_string(callback)

            def body(): return self._services.realm_services.evaluate_script(source)
        elif arguments:
            def body(): return callback(*arguments)
        else:
            body = callback
        if delay is pyv8.Undefined:
            delay = 0
        milliseconds = float(integer(delay, signed=True))
        ident = 0

        def run():
            logger.debug("{} {} runs", name, ident)
            return body()

        ident = self._services.task_services.schedule(run, milliseconds / 1000.0, repeat=repeat)
        logger.debug("{} {} scheduled in {} ms ({})", name, ident, milliseconds,
                     "string" if source is not None else "function")
        return ident

    def fn_clearTimeout(self, ident=0, *_extra):
        self._cancel_timer(ident, "clearTimeout")

    def fn_clearInterval(self, ident=0, *_extra):
        self._cancel_timer(ident, "clearInterval")

    def fn_requestAnimationFrame(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'requestAnimationFrame', 1, 0, constructor=False)
        frames = self._animation_frames()
        ident = frames["ident"]
        frames["ident"] = ident + 1
        frames["callbacks"][ident] = callback_value(callback)
        if frames["timer"] is None:
            # One timer per frame, not per callback: a frame is a turn every
            # request made before it shares, which is how a compositor paces them.
            frames["timer"] = self._services.task_services.schedule(
                self._run_frame, self._frame_delay(frames), repeat=False)
        logger.debug("requestAnimationFrame {} queued for frame {}", ident, frames["timer"])
        return ident

    def fn_cancelAnimationFrame(self, ident=0, *_extra):
        value = int(integer(ident, signed=True))
        logger.debug("cancelAnimationFrame {}", value)
        frames = self._animation_frames()
        if frames["callbacks"].pop(value, None) is None:
            return
        if not frames["callbacks"] and frames["timer"] is not None:
            self._services.task_services.cancel(frames["timer"])
            frames["timer"] = None

    def _animation_frames(self):
        frames = getattr(self, "_frames", None)
        if frames is None:
            frames = self._frames = {"callbacks": {}, "timer": None, "ident": 1, "due": None}
        return frames

    def _frame_delay(self, frames):
        """Seconds until the next frame falls due.

        The frame grid outlives a single turn, so a late one shifts no later frame.
        """
        now = clock_of(self._services).now_ms()
        due = frames["due"]
        if due is None or due < now:
            due = frames["due"] = now + ANIMATION_FRAME_MS
        return max(0.0, (due - now) / 1000.0)

    def _run_frame(self):
        """Every callback asked for since the last frame, on the frame's own reading."""
        frames = self._animation_frames()
        frames["timer"] = None
        callbacks = list(frames["callbacks"].values())
        frames["callbacks"].clear()
        if not callbacks:
            return
        now = clock_of(self._services).now_ms()
        # The frame's start rather than the moment it ran: callbacks asked for before
        # it agree on the reading, and one asked for inside it lands on the next frame.
        timestamp = min(frames["due"] or now, now)
        # A frame that overran its slot is counted out rather than replayed.
        frames["due"] = max(timestamp + ANIMATION_FRAME_MS, now)
        for callback in callbacks:
            try:
                callback(timestamp)
            except BaseException as error:  # one faulty frame must not stop the rest
                self._services.realm_services.report_exception(error)

    def fn_queueMicrotask(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error(self._scheduling_interface, 'queueMicrotask', 1, 0, constructor=False)
        self._services.realm_services.enqueue_microtask(callback_value(callback))

    def _cancel_timer(self, ident, name):
        value = int(integer(ident, signed=True))
        logger.debug("{} {}", name, value)
        self._services.task_services.cancel(value)

__all__ = ["_SchedulingMixin"]
