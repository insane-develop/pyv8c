"""Platform-owned V8 realms for pages and workers."""

import time

import pyv8

from pyv8.flag import V8Attribute

from ..engine.trace import log_trace
from ..foundation import promises
from ..foundation.executor import RealmExecutor
from ..foundation.tasks import TaskQueue
from ..web.bom.chrome import install_window_chrome
from ..web.bom.legacy_factories import install_legacy_factories
from .services import WorkerRealmServices
from .sessions import BindingSession


def _context_kwargs(session, context_type, trace, on_materialize=None, trace_callback=None, traceable=False, exposure_type=None):
    """Shared native Context arguments assembled from one binding session."""
    return {
        "trace": trace,
        "traceable": traceable,
        "trace_callback": (log_trace if trace_callback is None else trace_callback) if trace or traceable else None,
        "ctx_type": context_type,
        "lazy_factory": session.lazy_factory(exposure_type or context_type, on_materialize=on_materialize),
    }


class _RealmTaskHost:
    """The only two questions a worker's task queue may ask its realm.

    The adapter keeps ``foundation``'s queue from reaching the realm object.
    """

    def __init__(self, realm):
        self._realm = realm

    def is_closed(self):
        return self._realm.is_closed()

    def parser_blocked(self):
        # A worker has no parser to gate its task sources on.
        return False


def _publish_window_name(context, page):
    """Write the window's name as host work, so no trace event reports it."""
    tracing = bool(getattr(context, "trace", False))
    if tracing:
        context.trace = False
    try:
        page.window._publish_name()
    finally:
        if tracing:
            context.trace = True


#: Names Chrome hides in a realm that is not cross-origin isolated: the engine hands
#: ``SharedArrayBuffer`` to every context, so such a realm takes it back.
_NON_ISOLATED_HIDDEN = ("SharedArrayBuffer",)


def _declare_realm_interfaces(context, *, context_type, bindings):
    """Declare the realm's plan; the realm answers each name itself."""
    plan = bindings.plan(context_type)
    declared = context.install(plan.installs)
    if context_type == "Window":
        for alias, target in bindings.idl.window_aliases().items():
            if target in plan.installs:
                context.define_global(alias, context.glob[target],
                                      attributes=V8Attribute.DontEnum)
    for name in _NON_ISOLATED_HIDDEN:
        context.hide_global(name)
    return declared


class WorkerRealm:
    """Own the worker isolate, context, task queue and binding session."""

    def __init__(self, page, config):
        self.page = page
        self.config = config
        #: Written by the runtime that starts the worker; the realm only reads it.
        self.script_url = ""
        self.isolate = None
        self.context = None
        self.scope = None
        self.scripts = None
        # A worker global owns its task queue and drives it from its own owner
        # thread, so timers, microtasks and posted messages run there.
        self.tasks = TaskQueue(_RealmTaskHost(self))
        self.services = WorkerRealmServices(self.tasks, self)

    def is_closed(self):
        return self.isolate is None

    def initialize(self, scope):
        self.scope = scope
        config = self.config
        session = config.session
        session.prepare("Worker")
        self.isolate = pyv8.Isolate()
        self.context = self.isolate.context(
            scope, **_context_kwargs(session, "Worker", config.trace,
                                     trace_callback=config.browser.trace_callback,
                                     traceable=config.browser.traceable),
        )
        _declare_realm_interfaces(
            self.context,
            context_type="Worker",
            bindings=config.bindings,
        )
        return self.context

    # The worker's global scope reaches these through WorkerRealmServices, as a
    # page reaches its own through PageServices.  The realm owns the context, so
    # it is also what decides how a promise behaves once that context is gone.

    def evaluate_script(self, source):
        return self.context.exec_js(source)

    def enqueue_microtask(self, callback):
        return promises.enqueue_microtask(self.context, callback)

    def make_promise(self):
        return promises.make_promise(self.context)

    def promise_resolve(self, value):
        return promises.promise_resolve(self.context, value)

    def close(self):
        first_error = None

        def attempt(callback):
            nonlocal first_error
            try:
                callback()
                return True
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                return False

        attempt(self.tasks.close)
        if self.context is not None and attempt(self.context.close):
            self.context = None
        attempt(self.config.session.detach)
        if self.context is None:
            self.scope = None
        if self.isolate is not None and attempt(self.isolate.close):
            self.isolate = None
        if first_error is not None:
            raise first_error


class PageRealm:
    """Own the V8 context and binding session for one page."""

    def __init__(self, page, executor=None, previous_context=None):
        self.page = page
        self.context = None
        self._previous_context = previous_context
        self.executor = executor if executor is not None else RealmExecutor(name="pyv8c-page")
        #: False when the page owns the thread and hands it across navigations.
        self._owns_executor = executor is None
        browser = page.context_owner.browser
        # Web objects built through this session receive PageServices, not the
        # platform Page.
        self.binding_session = BindingSession(browser.bindings, page.services)
        page.services.bind_realm(self)
        try:
            if pyv8.current_context() is not None:
                # Asked for inside a running script, where waiting on the realm's
                # own thread would deadlock; an inserted frame's window must exist
                # before the inserting script returns.
                self._initialize()
            else:
                self.executor.call(self._initialize)
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        page = self.page
        browser = page.context_owner.browser
        binding_started = time.perf_counter_ns()
        self.binding_session.prepare("Window")
        binding_prepared = time.perf_counter_ns()
        #: A frame's realm is not the top page's: the label says which it is.
        context_type = "FRAME" if page.parent_page is not None else "TOP"
        self.context = pyv8.Context(page.window, _reuse_global_from=self._previous_context, **_context_kwargs(
            self.binding_session, context_type, page.trace,
            exposure_type="Window",
            on_materialize=self._record_deferred_materialized,
            trace_callback=page.context_owner.trace_callback,
            traceable=page.context_owner.traceable,
        ))
        self._previous_context = None
        self.executor.attach_deadline_source(self.context)
        context_created = time.perf_counter_ns()
        deferred_globals = _declare_realm_interfaces(
            self.context,
            context_type="Window",
            bindings=browser.bindings,
        )
        deferred_declared_at = time.perf_counter_ns()
        # Chromium installs window.chrome before any page script runs.
        install_window_chrome(self.context, page.services)
        _publish_window_name(self.context, page)
        install_legacy_factories(
            self.context, session=self.binding_session,
            table=browser.bindings.idl, document=page.document,
        )
        chrome_installed_at = time.perf_counter_ns()
        # The realm owns the context and the page's script controller borrows it,
        # so the isolate's error hooks and every Web API promise share one realm;
        # the hooks route exceptions V8 raises on its own.
        self.context.set_error_hooks(
            page.report_uncaught_message, page.rejections.record,
        )
        plan = self.binding_session.bindings.plan("Window")
        browser._record_startup(
            binding_binder_ms=(binding_prepared - binding_started) / 1_000_000,
            context_create_ms=(context_created - binding_prepared) / 1_000_000,
            deferred_global_declare_ms=(deferred_declared_at - context_created) / 1_000_000,
            chrome_install_ms=(chrome_installed_at - deferred_declared_at) / 1_000_000,
            core_interfaces=len(self.binding_session.materialized_interfaces),
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            known_interfaces=len(plan.installs),
            deferred_globals=deferred_globals,
        )

    def _record_deferred_materialized(self, name):
        """Keep startup stats in sync with on-demand interface materialisation."""
        self.page.context_owner.browser._record_startup(
            materialized_interfaces=len(self.binding_session.materialized_interfaces),
            deferred_materialized=len(self.binding_session.materialized_interfaces)
            - len(self.binding_session.bindings.plan("Window").ready),
        )

    def close(self):
        self.close_context()
        if self._owns_executor:
            self.executor.close()

    def enqueue_microtask(self, callback):
        return promises.enqueue_microtask(self.context, callback)

    def make_promise(self):
        return promises.make_promise(self.context)

    def promise_resolve(self, value):
        return promises.promise_resolve(self.context, value)

    def close_context(self):
        """Close the document-scoped parts: the context and the binding session.

        ``self.context`` going to ``None`` is what tells a late caller -- a promise of the
        replaced document, a microtask of the old realm -- that there is nothing to settle.
        """
        if self.executor.closed:
            return

        def close_context():
            context = self.context
            close = getattr(context, "close", None)
            if callable(close):
                close()
            self.context = None
            self.binding_session.detach()

        self.executor.call(close_context)


__all__ = ["PageRealm", "WorkerRealm"]
