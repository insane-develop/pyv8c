"""Page-owned resource collections used during teardown."""


class Ownership:
    __slots__ = ("websockets", "workers", "frames")

    def __init__(self):
        self.websockets = set()
        self.workers = set()
        self.frames = set()

    def track(self, kind, obj):
        getattr(self, kind).add(obj)

    def discard(self, kind, obj):
        getattr(self, kind).discard(obj)

    def drain(self, kind):
        bucket = getattr(self, kind)
        items = list(bucket)
        bucket.clear()
        return items
