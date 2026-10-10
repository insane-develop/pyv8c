"""Platform-owned service objects handed to web-facing interface code.

``PageServices`` assembles the six capability facets web code consumes; web
packages reach platform functionality only through them, never through the Page.
"""

import threading
from dataclasses import dataclass
from typing import Any

from ..foundation.clock import Clock
from ..foundation.urls import origin_string, serialize_origin
from .request_headers import ACCEPT_ENCODING, accept_language
from .sessions import BindingSession
from .state import Ownership


@dataclass(frozen=True)
class WorkerContext:
    """Everything a worker thread needs from its parent page."""

    browser: Any
    session: Any
    bindings: Any
    environment: Any
    trace: bool
    expose_unimplemented: bool
    stub_logging: str
    devtools: Any | None
    debug_parent: Any | None


class _TaskSource:
    """Task-queue access shared by the capabilities that post to the loop.

    A worker realm drives its own queue with the same capabilities a page does.
    """

    def __init__(self, tasks):
        self._tasks = tasks

    @property
    def closed(self):
        return self._tasks.closed

    def post_task(self, callback):
        self._tasks.post(callback)


class _PerformanceCapability:
    """What this realm measured, as records the web layer turns into entries.

    The platform times a document and the resources it fetched, and knows nothing about
    a ``PerformanceEntry``: a record is a plain dict, and the entry the web layer builds
    from it reads that dict live, so a navigation still growing reports what it reached.
    """

    def __init__(self):
        self._records = []
        self._listeners = []
        self.clock = Clock()

    def begin_navigation(self, url, redirects=0, redirects_span=0.0):
        """Start a new document's timeline: its navigation record, and nothing else.

        *redirects* is how many hops the document's own request followed and
        *redirects_span* how long they took, which the record reports as the window
        before the document starts; the hops lie before this timeline's origin.
        """
        self.reset()
        self.clock.reset()
        return self.record("navigation", name=str(url), start=self.clock.now_ms(),
                           redirectCount=int(redirects),
                           redirectStart=0.0, redirectEnd=round(float(redirects_span), 3))

    def reset(self):
        del self._records[:]
        del self._listeners[:]

    def record(self, kind, **facts):
        record = {"kind": kind, **facts}
        self._records.append(record)
        for listener in tuple(self._listeners):
            listener()
        return record

    def note(self, **milestones):
        """Update the document's own record with the milestones it reached."""
        record = self.navigation()
        if record is not None:
            record.update(milestones)

    def records(self, kind=""):
        if not kind:
            return tuple(self._records)
        return tuple(record for record in self._records if record["kind"] == kind)

    def navigation(self):
        for record in self._records:
            if record["kind"] == "navigation":
                return record
        return None

    def clear_resources(self):
        self._records = [record for record in self._records if record["kind"] != "resource"]

    def subscribe(self, listener):
        """Ask to be told when a record lands, so entries can be delivered."""
        if listener not in self._listeners:
            self._listeners.append(listener)


class _RealmCapability(_TaskSource):
    """Realm identity, scripting and task posting (implements RealmServices)."""

    def __init__(self, owner):
        super().__init__(owner._tasks)
        self._owner = owner
        #: The realm's timeline: what the platform timed for this document.
        self.performance = _PerformanceCapability()

    @property
    def url(self):
        return self._owner.window.location.href

    @property
    def environment(self):
        return self._owner.environment

    @property
    def origin(self):
        return self._owner._origin

    def make_promise(self):
        return self._owner._scripts.make_promise()

    def enqueue_microtask(self, callback):
        """Queue *callback* on the realm's V8 microtask queue.

        Promise reactions share it, which gives the one FIFO order the HTML
        specification asks for.
        """
        return self._owner._scripts.enqueue_microtask(callback)

    def evaluate_script(self, source):
        return self._owner._scripts.evaluate(source)

    def promise_resolve(self, value):
        return self._owner._scripts.promise_resolve(value)

    def report_exception(self, exc):
        """Report an uncaught exception through this realm's error reporting.

        The DOM calls page code, so an exception raised in a listener has no
        caller left to catch it: this is the specification's "report the
        exception", and the task queue's sink turns it into the ``error`` event.
        """
        self._tasks.report_error(exc)

    def materialize_interface(self, name):
        return self._owner._page_realm.binding_session.materialize(name)

    def ancestor_origins(self):
        """The origins of this realm's ancestors, nearest first.

        This is what ``Location.ancestorOrigins`` reports, so the chain is walked from
        the parent outwards.
        """
        origins = []
        page = getattr(getattr(self._owner, "_page_realm", None), "page", None)
        page = getattr(page, "parent_page", None)
        while page is not None:
            origins.append(serialize_origin(page.origin))
            page = getattr(page, "parent_page", None)
        return origins

    def interface_class(self, name):
        """The class this realm bound for *name*, or ``None`` when the baseline
        declares no such interface.  A foreign-namespace element is built from it, so
        the SVG and MathML interface modules need not exist to answer ``instanceof``.
        """
        session = self._owner._page_realm.binding_session
        if name not in session.bindings.idl.interfaces:
            return None
        return session.materialize(name)

    def heap_statistics(self):
        """V8's heap numbers for this realm's isolate, in bytes."""
        context = getattr(self._owner._page_realm, "context", None)
        return context.heap_statistics() if context is not None else {}

    def interface_materialized(self, name):
        return name in self._owner._page_realm.binding_session.materialized_interfaces

    def window_property(self, operation, name, value=None):
        """Operate on a same-origin Window in its owning realm."""
        realm = self._owner._page_realm
        def apply():
            target = realm.context.js_obj(self._owner.window)
            if operation == "get":
                try:
                    return target[name]
                except (KeyError, AttributeError):
                    # Reading a JavaScript object by key raises KeyError; only
                    # the attribute form raises AttributeError.
                    import pyv8
                    return pyv8.Undefined
            if operation == "set":
                target[name] = value
            elif operation == "delete":
                del target[name]
            elif operation == "query":
                return 0 if name in dir(target) else None
        # Cross-window access is synchronous and can start inside a V8 callback
        # holding the shared isolate lock: posting to another executor deadlocks.
        return apply()


class _TaskCapability(_TaskSource):
    """Event-loop task sources (implements TaskServices)."""

    def schedule(self, callback, delay, repeat=False):
        return self._tasks.schedule(callback, delay, repeat)

    def cancel(self, ident):
        self._tasks.cancel(ident)

    def begin_network(self):
        self._tasks.begin_network()

    def complete_network(self):
        self._tasks.complete_network()

    def begin_worker(self):
        self._tasks.begin_worker()

    def complete_worker(self):
        self._tasks.complete_worker()

    def begin_loader(self):
        self._tasks.begin_loader()

    def complete_loader(self):
        self._tasks.complete_loader()


class WorkerRealmServices:
    """The capability facets a worker hands to its own global scope.

    A worker realm owns its task queue and its V8 context, so timers behave as
    they do on a page; what the scope needs from its parent travels separately.
    """

    def __init__(self, tasks, runtime):
        self.task_services = _TaskCapability(tasks)
        self.realm_services = _WorkerRealmCapability(tasks, runtime)


class _WorkerRealmCapability(_TaskSource):
    """Worker scripting: the worker's own context, with no page behind it."""

    def __init__(self, tasks, runtime):
        super().__init__(tasks)
        self._runtime = runtime

    def heap_statistics(self):
        """V8's heap numbers for this realm's isolate, in bytes."""
        context = getattr(self._runtime._realm, "context", None)
        return context.heap_statistics() if context is not None else {}

    def materialize_interface(self, name):
        """Load the interface's feature and commit its config to this realm."""
        return self._runtime.config.session.materialize(name)

    @property
    def url(self):
        return self._runtime.script_url

    def evaluate_script(self, source):
        return self._runtime.evaluate_script(source)

    def enqueue_microtask(self, callback):
        return self._runtime.enqueue_microtask(callback)

    def make_promise(self):
        return self._runtime.make_promise()

    def promise_resolve(self, value):
        return self._runtime.promise_resolve(value)

    def report_exception(self, exc):
        """Route a listener exception to the worker's error reporting.

        A worker global has no window to report onto: the task queue forwards it
        to the parent page as the worker's ``error`` event.
        """
        self._tasks.report_error(exc)


class _ResourceCapability:
    """Resource loading and ownership (implements ResourceServices)."""

    def __init__(self, owner):
        self._owner = owner

    def load_resource(self, url, request=None):
        return self._owner._resources.load_resource(url, request)

    def load_resource_async(self, url, request, callback):
        # A request can finish before this call returns, so the two orders --
        # delivery first or registration first -- converge on one untrack.
        state = {"handle": None, "tracked": False, "done": False}
        lock = threading.Lock()

        def complete(response, error):
            with lock:
                state["done"] = True
                handle, tracked = state["handle"], state["tracked"]
            try:
                callback(response, error)
            finally:
                if handle is not None and tracked:
                    self._untrack_resource(handle)

        handle = self._owner._resources.load_resource_async(url, request, complete)
        with lock:
            state["handle"] = handle
            if handle is not None:
                self._track_resource(handle)
                state["tracked"] = True
            completed_early = state["done"]
        if completed_early and handle is not None:
            self._untrack_resource(handle)
        return handle

    def load_iframe(self, iframe):
        return self._owner._resources.load_iframe(iframe)

    def ensure_iframe_context(self, iframe):
        return self._owner._resources.ensure_iframe_context(iframe)

    def discard_iframe_context(self, iframe):
        return self._owner._resources.discard_iframe_context(iframe)

    def load_iframe_markup(self, iframe, markup):
        return self._owner._resources.load_iframe_markup(iframe, markup)

    def resolve_worker_source(self, script_url):
        return self._owner._resources.resolve_worker_source(script_url)

    def discover_element_resource(self, node):
        return self._owner._resources.discover_element_resource(node)

    def _track_resource(self, handle):
        if handle is not None:
            callback = handle.cancel
            self._owner._register_owned("resources", handle, callback)
            try:
                handle._scope_callback = callback
            except AttributeError:
                pass

    def _untrack_resource(self, handle):
        callback = getattr(handle, "_scope_callback", None)
        if callback is not None:
            self._owner._unregister_owned("resources", handle)
            try:
                handle._scope_callback = None
            except AttributeError:
                pass


class _DocumentCapability:
    """Document, window and parser hooks (implements DocumentServices)."""

    def __init__(self, owner):
        self._owner = owner

    @property
    def window(self):
        return self._owner.window

    @property
    def document(self):
        return self._owner.document

    def element_class(self, tag):
        return self._owner._navigation.element_class(tag)

    def install_inline_handler(self, node, name, source):
        return self._owner._scripts.install_inline_handler(node, name, source)

    def parser_script_start(self, node):
        return self._owner._navigation.parser_script_start(node)

    def parser_script_complete(self, node, source):
        return self._owner._navigation.parser_script_complete(node, source)

    def parser_is_blocked(self):
        return self._owner._navigation.parser_is_blocked()

    def write_markup(self, markup):
        return self._owner._navigation.write_markup(markup)

    def open_document(self):
        return self._owner._navigation.open_document()

    def close_document(self):
        return self._owner._navigation.close_document()

    def motion_event_listener(self, event_type):
        page = getattr(getattr(self._owner, "_page_realm", None), "page", None)
        if page is not None:
            page.lifecycle.motion_listener_added(str(event_type))

    def node_adopted(self, node):
        if getattr(node, "localName", "") == "iframe":
            if not node._is_connected():
                return None
            # An inserted frame gets its own context.  Built while a script runs, the realm is
            # queued instead: entering another realm re-enters this executor.
            services = self._owner._resources
            page = services.page
            if page.document.current_script is None:
                services.ensure_iframe_context(node)
            else:
                page.tasks.post(lambda: services.ensure_iframe_context(node))
            if (node.get_src()
                    and not getattr(node, "_resource_started", False)):
                services.discover_element_resource(node)
            return None
        return self._owner._scripts.node_adopted(node)

    def request_navigation(self, url, **options):
        return self._owner._navigation.request_navigation(url, **options)

    @property
    def parsing(self):
        return self._owner._navigation.parsing


class _WorkerCapability:
    """Worker spawning and tracking (implements WorkerServices)."""

    def __init__(self, owner):
        self._owner = owner

    def worker_configuration(self):
        browser = self._owner._context.browser
        session = BindingSession(browser.bindings, self._owner)
        return WorkerContext(
            browser=browser,
            session=session,
            bindings=browser.bindings,
            environment=self._owner.environment,
            trace=bool(self._owner.trace),
            expose_unimplemented=browser.expose_unimplemented,
            stub_logging=browser.stub_logging,
            devtools=self._owner._context._devtools,
            debug_parent=self._owner._page_realm.page._devtools_target,
        )

    def create_worker_realm(self, config):
        from .realms import WorkerRealm
        return WorkerRealm(self._owner, config)

    def track_worker(self, worker):
        self._owner._ownership.track("workers", worker)
        self._owner._register_owned("workers", worker, worker.fn_terminate)

    def discard_worker(self, worker):
        self._owner._ownership.discard("workers", worker)
        self._owner._unregister_owned("workers", worker)


class _ContextCapability:
    """Browser-context services: blobs, cookies, sockets (implements ContextServices)."""

    def __init__(self, owner):
        self._owner = owner

    def create_blob_url(self, blob, realm=None):
        return self._owner._context.create_blob_url(blob, realm=realm)

    def revoke_blob_url(self, url):
        self._owner._context.revoke_blob_url(url)

    def resolve_blob_url(self, url):
        return self._owner._context.resolve_blob_url(url)

    @property
    def cookie_jar(self):
        return self._owner._context.cookies

    @property
    def _databases(self):
        return self._owner._context._databases

    @property
    def network_available(self):
        return self._owner._context.network_backend is not None

    @property
    def http_available(self):
        backend = self._owner._context.network_backend
        return backend is not None and backend.http_available

    @property
    def ws_available(self):
        backend = self._owner._context.network_backend
        return backend is not None and backend.ws_available

    def websocket(self, url, protocols, callbacks):
        backend = self._owner._context.network_backend
        if backend is None:
            return None
        return backend.websocket(
            self._owner.task_services, url, protocols, callbacks, self._handshake(),
        )

    def _handshake(self):
        """What the page contributes to a WebSocket handshake; Chrome sends no client
        hint or ``Sec-Fetch-*`` on one, and the engine owns the ``Sec-WebSocket-*``."""
        environment = self._owner.environment
        options = {
            "headers": {
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "User-Agent": environment.user_agent,
                "Accept-Encoding": ACCEPT_ENCODING,
                "Accept-Language": accept_language(environment.languages),
            },
        }
        origin = origin_string(self._owner.realm_services.url)
        if origin != "null":
            options["origin"] = origin
        return options

    def close_websocket(self, websocket):
        backend = self._owner._context.network_backend
        if backend is not None:
            backend.close_websocket(websocket)

    def track_websocket(self, websocket):
        self._owner._ownership.track("websockets", websocket)
        self._owner._register_owned(
            "websockets", websocket,
            lambda ws=websocket: self.close_websocket(ws),
        )

    def discard_websocket(self, websocket):
        self._owner._ownership.discard("websockets", websocket)
        self._owner._unregister_owned("websockets", websocket)

    def discard_frame(self, frame):
        self._owner._ownership.discard("frames", frame)
        self._owner._unregister_owned("frames", frame)

    def track_frame(self, frame):
        self._owner._ownership.track("frames", frame)
        self._owner._register_owned("frames", frame, frame.close)
        try:
            frame._parent_scope_cleanup = lambda child=frame: self.discard_frame(child)
        except AttributeError:
            pass


class PageServices:
    """Composition root assembling the six realm capabilities.

    Holds controllers, the owning context and late-bound window/document
    references -- never the Page itself, which web code cannot reach through.
    """

    def __init__(self, context, environment, trace, ownership: Ownership,
                 navigation, scripts, resources, tasks, scope):
        self._context = context
        self.environment = environment
        self.trace = trace
        self._ownership = ownership
        # The exact callback object is kept, so a resource that completes
        # normally can unregister instead of leaving a closure behind.
        self._registrations = {}
        self._navigation = navigation
        self._scripts = scripts
        self._resources = resources
        self._tasks = tasks
        # Held directly: the task queue lives in foundation and must not be a
        # back door to the Page.
        self._scope = scope
        # Late-bound by Page once window/document/realm exist.
        self.window = None
        self.document = None
        self._page_realm = None
        self._origin = ("opaque", object())
        #: Granted ``sandbox`` tokens, or ``None`` when the realm is not sandboxed.
        self.sandbox_tokens = None

        # Narrow capabilities handed to web packages.
        self.realm_services = _RealmCapability(self)
        self.task_services = _TaskCapability(tasks)
        self.resource_services = _ResourceCapability(self)
        self.document_services = _DocumentCapability(self)
        self.worker_services = _WorkerCapability(self)
        self.context_services = _ContextCapability(self)

    def _register_owned(self, kind, resource, callback):
        key = (kind, id(resource))
        previous = self._registrations.get(key)
        if previous is not None:
            return previous[1]
        if self._scope.add(callback):
            self._registrations[key] = (resource, callback)
        return callback

    def _unregister_owned(self, kind, resource):
        key = (kind, id(resource))
        registration = self._registrations.pop(key, None)
        if registration is not None:
            self._scope.remove(registration[1])

    def bind_realm(self, page_realm):
        self._page_realm = page_realm

    def parent_window(self):
        """The window of the page this one is framed by; ``None`` at the top."""
        page = self._realm_page()
        parent = None if page is None else page.parent_page
        return None if parent is None else parent.window

    def top_window(self):
        """The window of this page's top-level page; ``None`` when it is that page."""
        page = self._realm_page()
        top = None if page is None else page.top_page
        return None if top is None or top is page else top.window

    def _realm_page(self):
        realm = self._page_realm
        return None if realm is None else realm.page


__all__ = ["PageServices", "WorkerContext", "WorkerRealmServices"]
