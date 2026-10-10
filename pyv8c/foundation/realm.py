"""Runtime contracts available to browser-facing interface objects.

Web API implementations consume realm services through :class:`RealmServices` instead
of importing ``platform`` or reaching into Page privates; each of the other protocols
covers one slice of the realm a page exposes.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RealmServices(Protocol):
    """Identity, scripting and promise settlement of one JS realm."""

    url: str
    origin: Any
    closed: bool

    def post_task(self, callback) -> None: ...
    def enqueue_microtask(self, callback) -> None: ...
    def report_exception(self, exc: BaseException) -> None:
        """Report an exception raised in a callback the DOM invoked."""
        ...
    def promise_resolve(self, value: Any) -> Any: ...
    def evaluate_script(self, source: str) -> Any: ...
    def make_promise(self) -> Any:
        """Return ``(promise, resolve, reject)`` in this realm's context."""
        ...


@runtime_checkable
class TaskServices(Protocol):
    """Task sources of one realm event loop."""

    closed: bool

    def post_task(self, callback) -> None: ...
    def schedule(self, callback, delay: float, repeat: bool = False) -> int: ...
    def cancel(self, ident: int) -> None: ...
    def begin_network(self) -> None: ...
    def complete_network(self) -> None: ...
    def begin_worker(self) -> None: ...
    def complete_worker(self) -> None: ...
    def begin_loader(self) -> None: ...
    def complete_loader(self) -> None: ...


@runtime_checkable
class ResourceServices(Protocol):
    """Loading documents, subresources and worker sources for a realm."""

    def load_resource(self, url, request=None) -> Any: ...
    def load_resource_async(self, url, request, callback) -> Any: ...
    def resolve_worker_source(self, script_url: str) -> str: ...
    def load_iframe(self, iframe) -> Any: ...
    def discover_element_resource(self, node) -> None: ...


@runtime_checkable
class DocumentServices(Protocol):
    """Document, window and parser hooks of the realm's active document."""

    window: Any
    document: Any
    parsing: bool

    def element_class(self, tag: str) -> type: ...
    def install_inline_handler(self, node, name, source) -> None: ...
    def node_adopted(self, node) -> None: ...
    def parser_script_start(self, node) -> None: ...
    def parser_script_complete(self, node, source) -> None: ...
    def parser_is_blocked(self) -> bool: ...
    def write_markup(self, markup: str) -> bool: ...


@runtime_checkable
class WorkerServices(Protocol):
    """Spawning and tracking dedicated workers of one realm."""

    def worker_configuration(self) -> Any: ...
    def create_worker_realm(self, config) -> Any: ...
    def track_worker(self, worker) -> None: ...
    def discard_worker(self, worker) -> None: ...


@runtime_checkable
class ContextServices(Protocol):
    """Browser-context level services: blob URLs and sockets."""

    def create_blob_url(self, blob, realm=None) -> str: ...
    def revoke_blob_url(self, url) -> None: ...
    def resolve_blob_url(self, url) -> Any: ...

    @property
    def ws_available(self) -> bool:
        """Whether this context has an engine to open sockets through."""
        ...

    def websocket(self, url, protocols, callbacks): ...
    def close_websocket(self, websocket) -> None: ...
    def track_websocket(self, websocket) -> None: ...
    def discard_websocket(self, websocket) -> None: ...


__all__ = [
    "RealmServices", "TaskServices", "ResourceServices", "DocumentServices",
    "WorkerServices", "ContextServices", "realm_of", "post_or_call",
]


def realm_of(obj) -> Any:
    """Return the realm an interface object belongs to, or ``None``.

    Objects hold either a realm capability directly or the page's service bundle via
    ``_realm``/``_services``; only the bundle's ``realm_services`` facet goes to the
    binding engine.
    """
    services = getattr(obj, "_realm", None)
    if services is None:
        services = getattr(obj, "_services", None)
    return getattr(services, "realm_services", services)


def post_or_call(services, callback, *args) -> None:
    """Deliver ``callback`` through the task queue, or run it when there is none.

    Web API objects are routinely built without a realm (tests, bare-environment entry
    points) and have no ``task_services``, but the DOM must still progress, so the
    callback runs synchronously.  A ``None`` callback is a no-op.
    """
    if callback is None:
        return
    task_services = getattr(services, "task_services", None) if services is not None else None
    if task_services is None:
        callback(*args)
    else:
        task_services.post_task(lambda: callback(*args))
