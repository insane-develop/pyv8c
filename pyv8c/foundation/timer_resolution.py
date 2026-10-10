"""Raise the platform timer resolution while a realm sleeps towards a deadline.

Windows schedules waits on its 64 Hz tick, so ``Event.wait(0.005)`` returns after
~15.6 ms and a ``setTimeout(f, 5)`` fired a tick late; Chromium raises the resolution
for the same reason.  The request is reference counted and scoped to deadline waits,
so a busy or idle host pays neither the timer overhead nor the power cost.  A no-op
off Windows.
"""

from __future__ import annotations

import contextlib
import sys
import threading

#: Milliseconds to ask for; 1 ms is the finest the platform offers.
_REQUESTED_MS = 1

_lock = threading.Lock()
_holders = 0
_winmm = None


def _library():
    global _winmm
    if _winmm is None:
        import ctypes

        _winmm = ctypes.windll.winmm
    return _winmm


def acquire():
    """Ask the platform for a millisecond timer; nesting is reference counted."""
    if sys.platform != "win32":
        return
    global _holders
    with _lock:
        if _holders == 0:
            _library().timeBeginPeriod(_REQUESTED_MS)
        _holders += 1


def release():
    """Drop one request, releasing the platform timer when none is left."""
    if sys.platform != "win32":
        return
    global _holders
    with _lock:
        if _holders == 0:
            return
        _holders -= 1
        if _holders == 0:
            _library().timeEndPeriod(_REQUESTED_MS)


@contextlib.contextmanager
def hold(enabled=True):
    """Hold the resolution for the duration of the block when *enabled*."""
    if not enabled:
        yield
        return
    acquire()
    try:
        yield
    finally:
        release()


__all__ = ["acquire", "hold", "release"]
