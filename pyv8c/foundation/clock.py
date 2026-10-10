"""The clock performance entries are timed with: an epoch origin, and offsets from it."""

import time


class Clock:
    """One document's timeline: an epoch a page can compare against ``Date.now()``,
    and the monotonic reading the entry offsets count from.  A navigation resets both."""

    #: Chrome reports time in whole 100-microsecond steps, and a page reads the
    #: difference: a finer clock is a fingerprint of its own.
    RESOLUTION_MS = 0.1

    def __init__(self):
        self._epoch = time.time() * 1000.0
        self._monotonic = time.perf_counter()

    def time_origin(self):
        """The epoch milliseconds ``performance.timeOrigin`` reports."""
        return self._epoch

    def now_ms(self):
        """Milliseconds since :meth:`time_origin`, at the resolution the platform has."""
        elapsed = (time.perf_counter() - self._monotonic) * 1000.0
        # Rounding is monotonic, so readings still never run backwards.
        return round(elapsed / self.RESOLUTION_MS) * self.RESOLUTION_MS

    def reset(self, epoch_ms=None):
        """Start a new timeline, as a document's navigation does."""
        self._epoch = time.time() * 1000.0 if epoch_ms is None else float(epoch_ms)
        self._monotonic = time.perf_counter()


def clock_of(services):
    """The clock of the document *services* belongs to, or a shared fallback."""
    realm = getattr(services, "realm_services", services)
    performance = getattr(realm, "performance", None)
    clock = getattr(performance, "clock", None)
    return clock if clock is not None else FALLBACK


#: For callers constructed without services.  Nobody resets it, so readings from it
#: stay consistent -- the behaviour the process-wide clock used to have.
FALLBACK = Clock()
