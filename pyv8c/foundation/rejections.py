"""The specification's unhandled-rejection algorithm, driven by V8.

V8 notices a rejection without a handler before its turn ends and revokes the notice if
a handler appears by the microtask checkpoint, so a notice is a *candidate* held until
the realm owner thread is back in Python.  Lives in ``foundation`` because both a page
and a worker need it and workers may not import ``platform``.
"""


class RejectionTracker:
    """Queue one realm's unhandled-rejection candidates."""

    def __init__(self, publish):
        #: Called as ``publish(event_type, promise, reason)``.
        self._publish = publish
        #: Candidates by promise identity, as ``(promise, reason)``.
        self._pending = {}
        #: ``rejectionhandled`` notifications waiting for the same boundary.
        self._followups = []

    def close(self):
        self._pending.clear()
        self._followups.clear()
        self._publish = None

    def record(self, event_type, identity, promise, reason):
        """Take one notification from the isolate's rejection hook."""
        if self._publish is None:
            return
        if event_type == "unhandledrejection":
            self._pending[identity] = (promise, reason)
            return
        if self._pending.pop(identity, None) is not None:
            # The handler appeared within the same turn, so the page never saw it.
            return
        # Published already: queue the paired event so both reach the page from the
        # same boundary, in the specification's order.
        self._followups.append((promise, reason))

    def flush(self):
        """Publish what the turn that just ended left owing the page.

        Called after every command and turn, so the common case -- nothing pending --
        must stay a cheap check with no allocation.
        """
        if not self._pending and not self._followups:
            return
        pending, self._pending = self._pending, {}
        followups, self._followups = self._followups, []
        for promise, reason in pending.values():
            if self._publish is None:
                return
            self._publish("unhandledrejection", promise, reason)
        for promise, reason in followups:
            if self._publish is None:
                return
            self._publish("rejectionhandled", promise, reason)


__all__ = ["RejectionTracker"]
