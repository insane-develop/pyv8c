"""Small, thread-safe lifecycle primitives shared by runtime resources."""

from __future__ import annotations

import threading
from enum import Enum


class LifecycleState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class ResourceScope:
    """Own cleanup callbacks and make close transitions idempotent."""

    def __init__(self):
        self._state = LifecycleState.OPEN
        self._callbacks = []
        self._lock = threading.RLock()

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def closed(self):
        return self.state is not LifecycleState.OPEN

    def add(self, callback):
        """Register cleanup, or run it immediately after closing begins."""
        with self._lock:
            if self._state is LifecycleState.OPEN:
                self._callbacks.append(callback)
                return True
        callback()
        return False

    def remove(self, callback):
        """Remove a previously registered callback if it is still active."""
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                return False
            return True

    def begin_close(self):
        with self._lock:
            if self._state is not LifecycleState.OPEN:
                return None
            self._state = LifecycleState.CLOSING
            return tuple(reversed(self._callbacks))

    def close(self):
        with self._lock:
            if self._state is LifecycleState.CLOSED:
                return
            if self._state is LifecycleState.OPEN:
                self._state = LifecycleState.CLOSING
            callbacks = tuple(reversed(self._callbacks))
            self._callbacks.clear()
        if not callbacks and self.state is LifecycleState.CLOSING:
            with self._lock:
                self._state = LifecycleState.CLOSED
            return
        for callback in callbacks:
            try:
                callback()
            except BaseException:  # cleanup must continue
                # Aborting teardown here would strand every resource below this one.
                pass
        with self._lock:
            self._state = LifecycleState.CLOSED


__all__ = ["LifecycleState", "ResourceScope"]
