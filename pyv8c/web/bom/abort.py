"""``AbortController`` and ``AbortSignal``: the token a page cancels work with.

One signal is shared by everything that can be cancelled -- a fetch, an XHR, a
listener registration -- so cancelling is a property of the work rather than of
the call that started it.
"""
import math

import pyv8
from ...idl import interface
from ...foundation.arguments import MISSING, abort_signal, is_nullish, missing_error, number, sequence
from ...foundation.arguments import IdlArgumentError
from ..page_errors import PageError, PageTypeError, dom_exception
from ..dom.events import Event, EventTarget, _install_handler_slots

__all__ = ["AbortController", "AbortSignal"]


#: Blink's wording for the reason a signal carries when the page named none.
ABORT_MESSAGE = "signal is aborted without reason"
#: And for the one a deadline carries.
TIMEOUT_MESSAGE = "signal timed out"


#: ``[EnforceRange] unsigned long long`` goes through a double, so Blink's range is
#: the one a double holds exactly rather than the type's own.
_UNSIGNED_LONG_LONG = 2**53 - 1


def _milliseconds(value):
    if not math.isfinite(number(value)):
        raise PageTypeError("Failed to execute 'timeout' on 'AbortSignal': Value is not of"
                          " type 'unsigned long long'.")
    if not 0 <= math.trunc(number(value)) <= _UNSIGNED_LONG_LONG:
        raise PageTypeError("Failed to execute 'timeout' on 'AbortSignal': Value is outside"
                          " the 'unsigned long long' value range.")
    return math.trunc(number(value))


@interface('AbortSignal', 'EventTarget')
class AbortSignal(EventTarget):
    """The token itself: aborted or not, the reason it was, and who listens."""

    __idl_overrides__ = {
        "members": {
            "abort": {
                "callback": "fn_static_abort"
            },
            "any": {
                "callback": "fn_static_any"
            },
            "timeout": {
                "callback": "fn_static_timeout"
            }
        },
        "interface": {
            "length": 0
        }
    }

    def __init__(self, services=None, **_):
        super().__init__(services=services)
        self._aborted = False
        self._reason = pyv8.Undefined
        self._subscribers = []

    def get_aborted(self): return self._aborted
    def get_reason(self): return self._reason

    def fn_throwIfAborted(self, *_extra):
        """Throw the reason, which is how a page opts out of a silent signal."""
        if not self._aborted:
            return pyv8.Undefined
        if isinstance(self._reason, BaseException):
            raise self._reason
        #: A reason that is not an exception -- a string, a JavaScript value -- crosses
        #: as the error its text describes, which is as close as Python comes to
        #: throwing the value itself.
        raise PageError(str(self._reason) or "Aborted")

    def _subscribe(self, callback):
        """Run *callback* on abort; a signal already aborted runs it at once."""
        if self._aborted:
            callback()
            return
        self._subscribers.append(callback)

    def _abort(self, reason=pyv8.Undefined):
        """Abort once: a second call keeps the first reason and fires nothing.

        A reason the page did give travels as it is, primitives included, so the
        page gets back exactly what it aborted with.
        """
        if self._aborted:
            return
        self._aborted = True
        self._reason = (dom_exception("AbortError", ABORT_MESSAGE)
                        if reason is pyv8.Undefined else reason)
        self.fn_dispatchEvent(Event("abort"))
        subscribers, self._subscribers = self._subscribers, []
        for callback in subscribers:
            callback()

    @staticmethod
    def fn_static_abort(_realm, reason=pyv8.Undefined, *_extra):
        return _aborted_signal(_realm, reason)

    @staticmethod
    def fn_static_any(_realm, signals=MISSING, *_extra):
        if signals is MISSING:
            raise PageTypeError("Failed to execute 'any' on 'AbortSignal': 1 argument"
                              " required, but only 0 present.")
        try:
            items = sequence(signals)
        except (PageTypeError, IdlArgumentError) as exc:
            raise PageTypeError("Failed to execute 'any' on 'AbortSignal': The provided"
                              " value cannot be converted to a sequence.") from exc
        combined = AbortSignal(services=_realm)
        for item in items:
            try:
                member = abort_signal(item)
            except (PageTypeError, IdlArgumentError) as exc:
                raise PageTypeError("Failed to execute 'any' on 'AbortSignal': Failed to"
                                  " convert value to 'AbortSignal'.") from exc
            if member is None:
                raise PageTypeError("Failed to execute 'any' on 'AbortSignal': Failed to"
                                  " convert value to 'AbortSignal'.")
            member._subscribe(lambda member=member: combined._abort(member._reason))
        return combined

    @staticmethod
    def fn_static_timeout(_realm, milliseconds=MISSING, *_extra):
        if milliseconds is MISSING:
            raise missing_error('AbortSignal', 'timeout', 1, 0, constructor=False)
        delay = _milliseconds(milliseconds)
        reason = dom_exception("TimeoutError", TIMEOUT_MESSAGE)
        signal = AbortSignal(services=_realm)
        if _realm is not None:
            _realm.task_services.schedule(lambda: signal._abort(reason), delay / 1000.0,
                                         repeat=False)
        return signal


def _aborted_signal(services, reason):
    signal = AbortSignal(services=services)
    signal._abort(reason)
    return signal


#: The accessor only; the baseline decides whether this release publishes ``onabort``.
_install_handler_slots(AbortSignal, ("onabort",))


@interface('AbortController', None)
class AbortController:
    """The page's handle on one signal: ``abort`` is the only way to fire it."""

    def __init__(self, services=None, **_):
        self._services = services
        self._signal = AbortSignal(services=services)

    def get_signal(self): return self._signal

    def fn_abort(self, reason=pyv8.Undefined, *_extra):
        self._signal._abort(reason)
