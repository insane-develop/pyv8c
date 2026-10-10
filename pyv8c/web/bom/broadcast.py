"""``BroadcastChannel``: one named channel per origin, shared by every realm in this process.

The Cloudflare widget opens ``new BroadcastChannel('a')`` and adds a ``message`` listener;
without an implementation the baseline stub answered that call with a Python
``AttributeError`` (``'_listeners'``), so the page caught an engine exception.

Chrome delivers a broadcast as a task on each *receiving* context's queue -- never on the
sender's -- so a channel inside a worker is reached from the page without the sender
running a handler.  The registry is per origin and lives in the process, like caches.
"""
from __future__ import annotations

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from ..page_errors import InvalidStateError
from ...foundation.urls import origin_string
from ..dom.events import EventTarget
from ..fetch.events import MessageEvent

__all__ = ["BroadcastChannel"]


#: origin -> channel name -> the live channels of that origin, in creation order.
_CHANNELS = {}


def _origin_of(services):
    """The origin *services* belongs to; a blob or worker URL reports its inner origin."""
    realm = getattr(services, "realm_services", None)
    return origin_string(str(getattr(realm, "url", "") or ""))


def _channels_of(services):
    return _CHANNELS.setdefault(_origin_of(services), {})


@interface('BroadcastChannel', 'EventTarget')
class BroadcastChannel(EventTarget):
    def __init__(self, name=MISSING, *_extra, services=pyv8.Undefined, _realm=None, **_):
        if name is MISSING:
            raise missing_error('BroadcastChannel', 'BroadcastChannel', 1, 0, constructor=True)
        services = None if services is pyv8.Undefined else services
        super().__init__(services=services or _realm)
        self._name = dom_string(name)
        self._closed = False
        self._table = _channels_of(self._services)
        self._table.setdefault(self._name, []).append(self)

    def get_name(self): return self._name
    def get_onmessage(self): return self._get_handler("onmessage")
    def get_onmessageerror(self): return self._get_handler("onmessageerror")
    def set_onmessage(self, value): self._set_handler("onmessage", value)
    def set_onmessageerror(self, value): self._set_handler("onmessageerror", value)

    def fn_postMessage(self, message=MISSING, *_extra):
        if message is MISSING:
            raise missing_error('BroadcastChannel', 'postMessage', 1, 0, constructor=False)
        if self._closed:
            raise InvalidStateError("Cannot send messages on a closed BroadcastChannel.")
        origin = _origin_of(self._services)
        for target in list(self._table.get(self._name, ())):
            if target is self or target._closed:
                continue
            services = target._services
            if services is None:
                continue
            services.realm_services.post_task(
                lambda target=target, message=message, origin=origin:
                target._deliver(message, origin))
        return None

    def fn_close(self, *_extra):
        """Close it: stop receiving, drop out of the registry, keep ``name``."""
        self._closed = True
        bucket = self._table.get(self._name)
        if bucket and self in bucket:
            bucket.remove(self)
        return None

    def _deliver(self, data, origin):
        if self._closed:
            return
        services = self._services
        if services is not None:
            services.realm_services.materialize_interface("MessageEvent")
        self.fn_dispatchEvent(MessageEvent("message", {"data": data, "origin": origin,
                                                       "ports": (), "isTrusted": True}))
