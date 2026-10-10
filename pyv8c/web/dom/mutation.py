"""``MutationObserver``: child-list and character-data records, delivered as a
microtask.  Attribute records come from ``setAttribute``/``removeAttribute``; a
reflected property that writes the attribute table directly is not observed."""

import pyv8

from ...idl import interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    callback_value,
    dictionary,
    missing_error,
)


@interface('MutationRecord', None)
class MutationRecord:
    def __init__(self, kind="", target=None, added=(), removed=(), previous=None,
                 next_=None, attribute_name=None, old_value=None, **_):
        from .collections import NodeList
        self._type, self._target = kind, target
        self._added = NodeList(values=list(added))
        self._removed = NodeList(values=list(removed))
        self._previous, self._next = previous, next_
        self._attribute_name, self._old_value = attribute_name, old_value

    def get_type(self): return self._type
    def get_target(self): return self._target
    def get_addedNodes(self): return self._added
    def get_removedNodes(self): return self._removed

    def get_previousSibling(self):
        return pyv8.Null if self._previous is None else self._previous

    def get_nextSibling(self):
        return pyv8.Null if self._next is None else self._next

    def get_attributeName(self):
        return pyv8.Null if self._attribute_name is None else self._attribute_name

    def get_attributeNamespace(self): return pyv8.Null

    def get_oldValue(self):
        return pyv8.Null if self._old_value is None else self._old_value


@interface('MutationObserver', None)
class MutationObserver:
    def __init__(self, callback=MISSING, *_extra, **_):
        if callback is MISSING:
            raise missing_error('MutationObserver', 'MutationObserver', 1, 0)
        self._callback = callback_value(callback)
        self._observations = []
        self._pending = []
        self._queued = False
        self._services = None

    def fn_observe(self, target=MISSING, options=None, *_extra):
        if target is MISSING:
            raise missing_error('MutationObserver', 'observe', 1, 0, constructor=False)
        settings = dictionary(options) if options is not None else {}
        self._observations.append(
            (target, {name: boolean(value) for name, value in settings.items()}))
        observe_document(target, self)

    def fn_disconnect(self, *_extra):
        self._observations.clear()
        self._pending.clear()

    def fn_takeRecords(self, *_extra):
        records, self._pending = self._pending, []
        return records

    def _wants(self, target, kind, options):
        """Whether one observation covers a *kind* change to *target*."""
        if options.get(kind):
            return True
        if kind == "childList":
            return bool(options.get("childList"))
        if kind == "attributes":
            return bool(options.get("attributes") or options.get("attributeFilter"))
        return bool(options.get("characterData"))

    def _record(self, kind, target, **details):
        self._pending.append(MutationRecord(kind, target, **details))
        if self._queued or self._services is None:
            return
        self._queued = True
        self._services.realm_services.enqueue_microtask(self._deliver)

    def _deliver(self):
        self._queued = False
        records, self._pending = self._pending, []
        if records:
            self._callback(records, self)


def observe_document(node, observer):
    """Register *observer* with the document *node* belongs to."""
    document = getattr(node, "ownerDocument", None) or node
    watchers = getattr(document, "_mutation_observers", None)
    if watchers is None:
        watchers = document._mutation_observers = []
    if observer not in watchers:
        watchers.append(observer)
    if observer._services is None:
        observer._services = getattr(node, "_services", None)


def record_mutation(target, kind, **details):
    """Hand one change to every observer watching *target*."""
    document = getattr(target, "ownerDocument", None) or target
    watchers = getattr(document, "_mutation_observers", None)
    if not watchers:
        return
    for observer in tuple(watchers):
        for node, options in observer._observations:
            if node is not target and not (options.get("subtree") and node.fn_contains(target)):
                continue
            if observer._wants(target, kind, options):
                observer._record(kind, target, **details)
            break

__all__ = ["MutationObserver", "MutationRecord", "observe_document", "record_mutation"]
