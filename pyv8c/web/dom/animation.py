"""Web Animations timelines: ``document.timeline`` and the interface it hangs from."""
import time


from ...idl import interface

__all__ = ["AnimationTimeline", "DocumentTimeline"]


@interface('AnimationTimeline', None)
class AnimationTimeline:
    def __init__(self, **_):
        self._start = time.monotonic()

    def get_currentTime(self):
        """Milliseconds since this timeline started."""
        return round((time.monotonic() - self._start) * 1000.0, 3)


@interface('DocumentTimeline', 'AnimationTimeline')
class DocumentTimeline(AnimationTimeline):
    """The timeline of one document; ``currentTime`` counts from its start."""
