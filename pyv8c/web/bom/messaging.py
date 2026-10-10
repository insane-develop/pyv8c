"""MessageChannel and its entangled pair of MessagePorts.

Both ports belong to the realm that created the channel, so a delivery is a task on
that realm's queue: the sending side never runs the receiving handler itself.
"""
from __future__ import annotations

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, is_nullish, missing_error
from ..dom.events import EventTarget
from ..fetch.clone import message_transfer, structured_clone, transfer_ports
from ..fetch.events import MessageEvent

__all__ = ["MessageChannel", "MessagePort"]


@interface('MessagePort', 'EventTarget')
class MessagePort(EventTarget):
    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services or _realm, **kw)
        self._twin = None
        #: A port queues what arrives before its queue is enabled, and flushes in order.
        self._pending = []
        #: What was posted before the far end existed, as a shared worker's port is.
        self._outbox = []
        self._enabled = False
        self._closed = False
        #: A transferred port is moved, not closed: the reference left behind only
        #: stops working, while the port itself carries on in its new realm.
        self._detached = False

    def _entangle(self, twin):
        self._twin = twin
        if twin is None:
            return
        outbox, self._outbox = self._outbox, []
        for message, ports in outbox:
            self._send(message, ports)

    def get_onmessage(self): return self._get_handler("onmessage")
    def get_onmessageerror(self): return self._get_handler("onmessageerror")
    def set_onmessage(self, value):
        self._set_handler("onmessage", value)
        if not self._enabled and not is_nullish(value):
            self.fn_start()
    def set_onmessageerror(self, value): self._set_handler("onmessageerror", value)

    def fn_postMessage(self, message=MISSING, options=None, *_extra):
        if message is MISSING:
            raise missing_error('MessagePort', 'postMessage', 1, 0, constructor=False)
        transfer = message_transfer(options)
        if self._closed or self._detached:
            return None
        #: A port carries a snapshot taken now, not a reference: the far realm builds
        #: its own data, so what the sender does to its object afterwards is not seen
        #: on the other side, and a port that outlives its realm still delivers plain
        #: values rather than objects of an isolate that is gone.
        data = structured_clone(message, context=pyv8.current_context())
        twin = self._twin
        # The ports travel into the realm the twin dispatches in, which is why the
        # transfer is read here and not where the twin receives it.
        ports = transfer_ports(transfer, services=twin._services if twin else None,
                               caller='MessagePort')
        if twin is None:
            self._outbox.append((data, ports))
            return None
        self._send(data, ports)
        return None

    def _detach(self):
        """Move this port away: the reference left behind stops working."""
        twin, self._twin = self._twin, None
        self._detached = True
        return twin

    def _moved_view(self, services):
        """The port as the realm it was moved into sees it, entangled with the twin."""
        twin = self._detach()
        view = MessagePort(services=services)
        view._twin = twin
        if twin is not None:
            twin._twin = view
        return view

    def _send(self, message, ports=()):
        twin = self._twin
        if twin is None or twin._closed or self._closed:
            return
        services = twin._services
        if services is not None:
            services.realm_services.post_task(lambda: twin._deliver(message, ports))

    def fn_start(self, *_extra):
        if self._enabled:
            return None
        self._enabled = True
        pending, self._pending = self._pending, []
        for data, ports in pending:
            self._deliver(data, ports)
        return None

    def fn_close(self, *_extra):
        self._closed = True
        self._pending.clear()
        return None

    def _deliver(self, data, ports=()):
        if self._closed:
            return
        if not self._enabled:
            self._pending.append((data, ports))
            return
        services = self._services
        if services is not None:
            services.realm_services.materialize_interface("MessageEvent")
        self.fn_dispatchEvent(MessageEvent("message", {"data": data, "origin": "",
                                                       "ports": ports, "isTrusted": True}))


@interface('MessageChannel', None)
class MessageChannel:
    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, **_):
        if services is pyv8.Undefined:
            services = None
        services = services or _realm
        self._port1 = MessagePort(services=services)
        self._port2 = MessagePort(services=services)
        self._port1._entangle(self._port2)
        self._port2._entangle(self._port1)

    def get_port1(self): return self._port1
    def get_port2(self): return self._port2
