"""The Page facade: one document, its controllers, and the API around them."""

from __future__ import annotations

import threading
from typing import Literal

import pyv8
from ..foundation.logging import logger

from ..foundation.arguments import is_nullish
from ..foundation.host_errors import HostStateError
from ..foundation.error_policy import is_host_failure
from ..foundation.lifecycle import ResourceScope
from ..foundation.rejections import RejectionTracker
from ..foundation.tasks import TaskQueue
from ..foundation.urls import origin_tuple
from ..web.bom.window import Window
from ..web.dom.document import Document, HTMLDocument
from ..web.dom.nodes import Node
from ..web.fetch.response import Response
from .lifecycle import DocumentLifecycle
from .navigation import NAVIGATION_GRACE, NavigationController
from .net import PageNet
from .realms import PageRealm, RealmExecutor
from .resource_loading import ResourceController
from .scripting import ScriptController
from .services import PageServices
from .state import Ownership


EMPTY_DOCUMENT = "<!DOCTYPE html><html><head></head><body></body></html>"


class _TaskHostAdapter:
    """Minimal host surface handed to the page's TaskQueue.

    The queue may only ask its host the two TaskHost questions, so holding a
    ``Page`` there would let foundation reach the whole platform object.
    """

    def __init__(self, page):
        self._page = page

    def is_closed(self):
        return self._page.closed

    def parser_blocked(self):
        return self._page.navigation.parser_is_blocked()


class _ContextBootstrapProxy:
    """Placeholder published while native Context installs templates."""

    def __init__(self):
        self.args = ()
        self.kw = {}

    def expose(self, *_args, **_kwargs):
        return None

    def exec_js(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


class Page:
    """One document and the host API around it.

    A page owns a realm -- a native V8 context with its Window and Document -- and
    is the supported way to reach it:     :meth:`evaluate` runs script,
    :attr:`window` and :attr:`document` are the host-side handles, :attr:`net`
    is the network API (``get``/``post`` and ``fetch``/``xhr``, described on
    :class:`~pyv8c.platform.net.PageNet`), :attr:`mouse` moves the pointer and
    clicks (with :meth:`click` and :meth:`hover` for the common case), and
    :attr:`url`/:attr:`origin` describe the current document.  Whatever a
    script sees is served inside the realm.

    Navigating replaces that realm, so a JS handle taken before a :meth:`goto` or
    :meth:`set_content` must not be used after it.

    These methods marshal onto the realm's owner thread, so they may be called
    from another thread; the JS objects they return may not (see
    ``docs/USAGE.md``).
    """

    def __init__(self, context, url="about:blank", *, trace=None, environment=None, parent=None, origin=None):
        self.context_owner = context
        self.parent_page = parent
        self.top_page = parent.top_page if parent is not None else self
        self.origin = tuple(origin) if origin is not None else origin_tuple(
            url, inherited=getattr(parent, "origin", None)
        )
        self.origin = context._blob_origins.get(str(url), self.origin)
        self._opaque_origin = origin is not None and self.origin[0] == "opaque"
        self.environment = context.browser.environment.overlay(environment)
        self.resource_interceptor = context.browser.resource_interceptor
        # Overriding the browser default here leaves the other pages' setting alone.
        self._trace = context.trace if trace is None else bool(trace)
        self.closed = False
        self._resource_scope = ResourceScope()
        self._close_lock = threading.Lock()
        self._closing = False
        self.navigation = NavigationController(self)
        self.lifecycle = DocumentLifecycle(self)
        self.scripts = ScriptController(self)
        self.resources = ResourceController(self)
        self.tasks = TaskQueue(_TaskHostAdapter(self))
        # An uncaught exception must surface as an ErrorEvent on the window.
        self._reporting_error = False
        self.tasks.set_error_sink(self.report_exception)
        # V8 reports a rejection before the turn that caused it ends, so the
        # candidates wait for the executor's next boundary.
        self.rejections = RejectionTracker(self.report_rejection)
        self._owned = Ownership()
        # Web-facing objects see this narrow surface, never the Page itself.
        self.services = PageServices(
            context, self.environment, self.trace, self._owned,
            self.navigation, self.scripts, self.resources, self.tasks,
            self._resource_scope,
        )
        self.services._origin = self.origin
        self.document = HTMLDocument(services=self.services, url=url, ready_state="complete")
        self.resource_loader = context.browser.resource_loader
        self.resource_interceptor = context.browser.resource_interceptor
        self.cache_enabled = context.browser.cache_enabled
        self.network_backend = context.network_backend
        self.window = Window(
            services=self.services,
            feature_runtime=context.browser.bindings.runtime,
        )
        self.services.window = self.window
        self.services.document = self.document
        self.window.location.href = str(url)
        self.net = PageNet(self)
        # Built on first use: the module reaches the layout model, which would
        # drag font measurement into a startup that does not need it yet.
        self._mouse = None
        # Native template installation can inspect the global object before
        # Context.__init__ returns, so publish a sentinel until the realm exists.
        self.context = _ContextBootstrapProxy()
        self._devtools_target = None
        #: One owner thread for the page's lifetime; navigations hand it to the
        #: next realm instead of starting another.
        self.executor = RealmExecutor(name="pyv8c-page")
        self.realm = PageRealm(self, executor=self.executor)
        self.services.bind_realm(self.realm)
        self.realm.executor.attach_task_queue(self.tasks)
        self.realm.executor.attach_rejection_tracker(self.rejections)
        self.context = self.realm.context
        Window._bind_context(self.context, self.window)

    @property
    def trace(self) -> bool:
        """Whether trace events are reported for this page."""
        return self._trace

    @trace.setter
    def trace(self, enabled: bool) -> None:
        """Switch tracing for this page.

        :param enabled: whether trace events should be reported.
        :raises TypeError: *enabled* is not a ``bool``.
        :raises RuntimeError: the Browser was not created traceable (the native
            context refuses the switch), or the page is already closed.
        """
        if not isinstance(enabled, bool):
            raise TypeError("trace must be a bool")

        def update():
            if self.closed or self.context_owner.closed:
                raise RuntimeError("page is closed")
            self.context.trace = enabled
            self._trace = enabled
            self.services.trace = enabled

        self.realm.executor.call(update)

    def _update_origin(self, url):
        if self._opaque_origin:
            return
        self.origin = origin_tuple(url, inherited=getattr(self.parent_page, "origin", None))
        self.origin = self.context_owner._blob_origins.get(str(url), self.origin)
        self.services._origin = self.origin
        self.window._origin = self.origin
        # The native access-check callback resolves the accessing realm from this
        # table, and navigation can change the origin after the Context exists.
        entry = Window._context_origins.get(id(self.context))
        if entry is not None:
            Window._context_origins[id(self.context)] = (entry[0], self.origin)

    # One entry point for everything that notices an exception -- a task that
    # raised, a listener that threw, an isolate hook -- so all of them surface on
    # the window as an ``error`` event instead of vanishing.
    def report_exception(self, error=None, *, message=None, filename="", line=0,
                         column=0, value=None):
        """Mirror an uncaught exception onto the realm's window.

        Either a Python *error*, which brings its own text and (usually) the
        JavaScript value behind it, or -- for an exception that never reached
        Python -- the *message*, position and thrown *value* V8 captured.

        The guard keeps a page handler that throws while handling this from
        re-entering the reporter.
        """
        if self.closed or self._reporting_error:
            return
        #: A script error is the page's; a failure of the host's own scheduling is
        #: not, so the window hears nothing and the reason stays in the log.
        if error is not None and is_host_failure(error):
            logger.warning("the host could not finish: {}", error)
            return
        self._reporting_error = True
        try:
            self.lifecycle.dispatch_script_error(
                None, error, message=message, filename=filename, line=line,
                column=column, value=value,
            )
        except BaseException:  # reporting a script error must not raise
            logger.debug("failed to report an exception", exc_info=True)
        finally:
            self._reporting_error = False

    def report_uncaught_message(self, message, source, line, column, error):
        """Report the pieces of an exception V8 raised on its own.

        This is the isolate's uncaught-exception hook: a microtask job that
        threw never returns to a Python frame, so V8 hands over the message, the
        throw site and the exception value it captured.
        """
        if is_nullish(source):
            source = ""
        self.report_exception(
            message=str(message), filename=str(source), line=int(line),
            column=int(column), value=error,
        )

    def report_rejection(self, event_type, promise, reason):
        """Mirror a rejection notice onto the window's rejection events."""
        if self.closed or self._reporting_error:
            return
        self._reporting_error = True
        try:
            self.lifecycle.dispatch_rejection(event_type, promise, reason)
        except BaseException:  # reporting a rejection must not raise
            logger.debug("failed to report a rejection", exc_info=True)
        finally:
            self._reporting_error = False

    @property
    def binding_session(self):
        return self.realm.binding_session

    @property
    def mouse(self) -> MouseInput:
        """The page's mouse: position, buttons and the events a move produces.

        Built on first read -- see :class:`~pyv8c.platform.input.MouseInput` --
        and shared for the life of the page, so the position carries over from
        one move to the next.
        """
        if self._mouse is None:
            from .input import MouseInput
            self._mouse = MouseInput(self)
        return self._mouse

    def _window_name_of(self, previous_window, previous_realm):
        """The browsing context's name, which a page may have set from script."""
        context = previous_realm.context
        if context is not None:
            try:
                return str(context.exec_js("String(this.name)"))
            except BaseException:
                pass
        return getattr(previous_window, "_window_name", "")

    def _renew_document_realm(self, url):
        """Give the page the Window and realm a new document comes with.

        Only browsing-context state comes along; a new realm is a new global
        object, so the last document's properties go with the last document.
        """
        previous_window, previous_realm = self.window, self.realm
        window = Window(
            services=self.services,
            feature_runtime=self.context_owner.browser.bindings.runtime,
        )
        window._frame_element = previous_window._frame_element
        window._window_name = self._window_name_of(previous_window, previous_realm)
        for slot in ("history", "localStorage", "sessionStorage"):
            window._features._cache(slot, previous_window._features.value(slot))
        self.window = window
        self.services.window = window
        self.realm = PageRealm(self, executor=self.executor)
        self.services.bind_realm(self.realm)
        self.realm.executor.attach_task_queue(self.tasks)
        self.realm.executor.attach_rejection_tracker(self.rejections)
        self.context = self.realm.context
        Window._bind_context(self.context, window)
        Window._unbind_context(previous_realm.context)
        # This command runs on the page's one owner thread, so the previous
        # realm's document-scoped parts close inline; the thread itself stays.
        try:
            previous_realm.close_context()
        except BaseException as error:
            self.report_exception(error)
        return window

    @property
    def url(self) -> str:
        """The current document's URL, as the Window's ``location.href`` shows it."""
        return self.window.location.href

    @url.setter
    def url(self, value: str) -> None:
        """Navigate to *value*; assigning here is :meth:`goto`."""
        self.goto(value)

    # Web-facing code is served by ``self.services``; controllers reach each
    # other through the Page attributes, so only user-facing forwards remain.

    def set_content(self,
                    html: str = EMPTY_DOCUMENT,
                    url: str = "about:blank") -> Page:
        """Replace the document with *html* and parse it right away.

        Parsing starts on the calling thread and continues until a
        parser-blocking script makes it wait; the navigation lifecycle is not
        replayed, so this is content injection rather than a navigation.

        :param html: markup for the new document; the default is an empty
            standards-mode document, while markup without a doctype
            (``"<body></body>"``) parses as ``BackCompat``.
        :param url: URL the new document reports.  Nothing is fetched from it, so
            pass the address the content claims to come from to keep the origin
            and relative URLs right.
        :returns: this page.
        :raises InvalidStateError: the page or its context is closed.
        """
        if pyv8.current_context() is not None:
            #: Asked for from inside a running script, so waiting on this page's thread would
            #: deadlock: the caller holds the isolate the commit's own parse needs.
            #: ``PageRealm`` answers the same way for a realm created inside a script.
            return self.navigation.set_content(html, url)
        return self.realm.executor.call(self.navigation.set_content, html, url)

    def goto(self,
             url: str,
             *,
             timeout: float | None = 30,
             wait_until: Literal["commit", "domcontentloaded", "load"] | None = "load") -> Response | None:
        """Load *url* as this page's document, the way a browser navigates.

        Loading goes through the resource loader (``resource_loader``, the
        interceptor, the cache), relative URLs resolve against the current URL,
        and the returned response is the document that was committed.

        :param url: absolute, or relative to the current document.
        :param timeout: seconds allowed for the document request and for waiting
            on *wait_until*.  ``None`` leaves the request unbounded and caps the
            wait at 30 seconds; negative values are treated as 0.
        :param wait_until: ``"commit"`` returns once the document is replaced;
            ``"domcontentloaded"``/``"load"`` wait for that lifecycle state.
            ``None`` is accepted and means ``"commit"``; ``"networkidle"`` is not
            supported.
        :returns: the document's
            :class:`~pyv8c.web.fetch.response.Response`, or ``None`` when
            navigation was refused -- for example by a ``beforeunload`` handler.
        :raises TimeoutError: the requested lifecycle state was not reached in
            time.
        :raises ValueError: *wait_until* is not one of the accepted values.
        :raises InvalidStateError: the page or its context is closed.
        """
        grace = None if timeout is None else max(0.0, float(timeout)) + NAVIGATION_GRACE
        return self.realm.executor.call(
            self._goto, url, timeout, wait_until, timeout=grace,
        )

    def _goto(self, url, timeout, wait_until):
        """The navigation itself, named so a timeout says which call it was."""
        return self.navigation.goto(url, timeout=timeout, wait_until=wait_until)

    def evaluate(self,
                 script: str,
                 filename: str | None = None) -> object:
        """Run *script* in the page's realm and return its result as Python.

        *script* is source, not an expression only: ``"1 + 1"`` works, and so does
        a statement list that ends in a value.  A promise is **not** awaited --
        wait with :meth:`run_until_idle` and read the settled result.

        :param script: JavaScript source.
        :param filename: name this script appears under in stack traces.
        :returns: the result converted to Python; a JS object becomes a ``dict``.
            Use :meth:`evaluate_handle` to keep a live object instead.
        :raises pyv8.JSException: the script threw.
        :raises RuntimeError: the page is closed.
        """
        def evaluate_data():
            value = self.scripts.evaluate(script, filename=filename)
            return value.to_python() if isinstance(value, pyv8.JSObject) else value
        return self.realm.executor.call(evaluate_data)

    def evaluate_handle(self,
                        script: str,
                        filename: str | None = None) -> pyv8.JSObject:
        """Run *script* and return the live V8 result instead of a Python copy.

        The way to reach values Python cannot copy -- a DOM node, a ``Map``, a
        function -- at the cost of holding a JS object: it belongs to the realm it
        came from, so navigation invalidates it and only the owner thread may
        touch it.

        :param script: JavaScript source.
        :param filename: name this script appears under in stack traces.
        :returns: a ``pyv8.JSObject``.
        :raises pyv8.JSException: the script threw.
        :raises RuntimeError: the page is closed.
        """
        return self.realm.executor.call(
            self.scripts.evaluate, script, filename=filename,
        )

    def run_until_idle(self, timeout: float = 0) -> None:
        """Turn the realm's event loop until it has no ready work left.

        A waiting helper for host code and tests rather than a loop of its own:
        the realm executor already runs the loop, and this blocks while tasks,
        timers and in-flight network, worker or loader work is still arriving.

        :param timeout: seconds to allow for work that is not ready yet (timers,
            in-flight requests).  ``0`` drains what is queued and returns at once.
            A positive value returns when the deadline passes without promising
            that everything finished; a never-ending script cannot be drained.
        :returns: ``None``.
        """
        self.realm.executor.call(self.tasks.run_until_idle, timeout)

    def dispatch_event(self,
                       event_type: str,
                       *,
                       target: str | object = "window",
                       selector: str | None = None,
                       event_class: str | None = None,
                       init: dict | None = None) -> bool:
        """Dispatch a host-trusted event and report whether it was not cancelled.

        Unlike a script-created event, the dispatched one is marked trusted, which
        is what lets page code react to it the way it reacts to a real one.

        :param event_type: the event's type (``"click"``, ``"custom"``, ...).
        :param target: ``"window"`` (default), ``"document"``, or a DOM node from
            :meth:`query_selector`.  Mutually exclusive with *selector*.
        :param selector: resolve the target from the current document instead.
        :param event_class: interface to build the event from; inferred from
            *event_type* when omitted (``"click"`` becomes ``MouseEvent``).
        :param init: plain dict of EventInit members; the ``bubbles``/
            ``cancelable`` defaults a browser would use are filled in when absent.
        :returns: ``True`` when the event was not cancelled, ``False`` when a
            listener called ``preventDefault``.
        :raises HostStateError: *selector* matches nothing.
        :raises TypeError: *event_class* is not an ``Event`` interface.
        """
        return self.realm.executor.call(
            self.lifecycle.dispatch_event, event_type,
            target=target, selector=selector, event_class=event_class, init=init,
        )

    def query_selector(self, selector: str) -> Node | None:
        """Return the host-side node matching a CSS selector.

        :param selector: CSS selector, evaluated against the current document.
        :returns: the DOM node handle, or ``None`` when nothing matches.
        """
        def resolve():
            node = self.document.fn_querySelector(str(selector))
            return None if node is pyv8.Null else node

        return self.realm.executor.call(resolve)

    def set_visibility(self, hidden: bool) -> bool:
        """Set the document's visibility and fire the events that follow.

        :param hidden: ``True`` hides the document (``visibilitychange``, then
            ``freeze``); ``False`` shows it (``visibilitychange``, then
            ``resume``).
        :returns: ``True`` when the state changed.  A redundant call changes
            nothing and returns ``False``, matching the browser's
            "only on transition" behaviour.
        """
        return self.realm.executor.call(self.lifecycle.set_visibility, hidden)

    def _require_element(self, selector: str):
        """The node *selector* matches, or the error a missing one is worth."""
        node = self.query_selector(selector)
        if node is None:
            raise HostStateError(f"No element matches the selector {selector!r}")
        return node

    def click(self,
              selector: str,
              *,
              button: str = "left",
              click_count: int = 1,
              force: bool = False,
              delay: float | None = None,
              duration: float | None = None) -> None:
        """Click the element *selector* matches, the way a user would.

        The mouse is moved to the element's centre and pressed and released
        there, so the page sees the whole sequence a real click produces: the
        boundary and move events on the way, focus, and finally ``click`` --
        with any activation behavior the element has (a link navigates, a
        checkbox toggles, a submit button submits, a label reaches its control).

        :param selector: a CSS selector, resolved against the current document.
        :param button: ``"left"``, ``"middle"`` or ``"right"``.
        :param click_count: the ``detail`` the press and the click report.
        :param force: click the centre even when another element is on top of
            it, which is the one thing that would otherwise refuse the click.
        :param delay: seconds the button stays down before it is released.
        :param duration: seconds the approach move is spread over.
        :raises HostStateError: *selector* matches nothing.
        :raises InvalidStateError: the element has no box to click, or its
            centre is covered by another element and *force* is false.
        """
        return self.mouse.click_element(
            self._require_element(selector), button=button, click_count=click_count,
            force=force, delay=delay, duration=duration,
        )

    def dblclick(self, selector: str, *, button: str = "left", force: bool = False,
                 delay: float | None = None, duration: float | None = None) -> None:
        """Double-click the element *selector* matches.

        Two clicks counted as one run, so the second reports ``detail`` 2 and a
        ``dblclick`` follows it.
        """
        node = self._require_element(selector)
        self.mouse.move_to_element(node, force=force, duration=duration)
        return self.mouse.dblclick(button=button, delay=delay)

    def hover(self, selector: str, *, force: bool = False,
              duration: float | None = None, curve: str | None = None,
              seed: int | None = None) -> None:
        """Move the mouse to the centre of the element *selector* matches.

        The element is left hovered, which is what the boundary events report:
        a page watching ``mouseover`` sees the move arrive.
        """
        return self.mouse.move_to_element(
            self._require_element(selector), force=force, duration=duration,
            curve=curve, seed=seed,
        )

    def close(self) -> None:
        """Close the page and everything it owns.

        Idempotent and safe to call from another thread; a concurrent caller is
        left to the owner thread's teardown instead of closing twice.  Frames,
        workers, WebSockets, pending resources, DevTools registration and the
        realm itself are released.  The first failure is remembered and re-raised
        after the rest was attempted, which leaves the page able to retry.  A
        closed page refuses further work: its realm executor raises
        ``RuntimeError``.
        """
        with self._close_lock:
            if self.closed:
                return
            # A concurrent close already runs the owner-thread teardown, so the
            # scope stays OPEN and a failed owner call can be retried.
            if self._closing:
                return
            self._closing = True
        # A paused inspector loop or a start-paused gate blocks the realm owner;
        # wake it before asking that owner to tear down synchronously.
        if not self.realm.executor.is_owner_thread():
            target = self._devtools_target
            if target is not None:
                target.wake_waiting_owner()
        try:
            return self.realm.executor.call(self._close_owner)
        finally:
            with self._close_lock:
                self._closing = False
            # The scope closes after all owner-thread cleanup; a dispatch that
            # timed out before starting leaves it OPEN for a later close().
            if self.closed:
                self._resource_scope.close()

    def _close_owner(self):
        if self.closed:
            return
        first_error = None

        def attempt(callback, *args):
            nonlocal first_error
            try:
                callback(*args)
                return True
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                return False

        if not getattr(self, "_unload_dispatched", False):
            self._unload_dispatched = True
            if self.navigation.has_navigation:
                attempt(self.lifecycle.dispatch_beforeunload)
                attempt(self.lifecycle.dispatch_page_hide, False)
                attempt(self.lifecycle.dispatch_unload)
        # Drop cached JS scripts before the V8 context goes away.
        self.scripts.clear_js_helpers()
        self.scripts.reset()
        if self._devtools_target is not None:
            if self.context_owner._devtools is not None:
                if attempt(self.context_owner._devtools.unregister, self):
                    self._devtools_target = None
            else:
                self._devtools_target = None
        attempt(self.net.close)
        attempt(self.tasks.close)
        attempt(self.resources.reset)
        self.rejections.close()
        self.lifecycle.load_waiters.clear()
        for frame in self._owned.drain("frames"):
            if attempt(frame.close):
                self.services.context_services.discard_frame(frame)
            else:
                self._owned.track("frames", frame)
        for ws in self._owned.drain("websockets"):
            if not self.network_backend or attempt(self.network_backend.close_websocket, ws):
                self.services.context_services.discard_websocket(ws)
            else:
                self._owned.track("websockets", ws)
        for worker in self._owned.drain("workers"):
            if attempt(worker.fn_terminate):
                self.services.worker_services.discard_worker(worker)
            else:
                self._owned.track("workers", worker)
        if first_error is not None:
            # Keep the realm owner available to retry failed native resources.
            raise first_error
        Window._unbind_context(self.context)
        self.realm.close()
        self.executor.close()
        self.context = None
        self.closed = True
        self.context_owner._discard_page(self)
        parent_cleanup = getattr(self, "_parent_scope_cleanup", None)
        if callable(parent_cleanup):
            parent_cleanup()
            self._parent_scope_cleanup = None
        # Idempotent: closes callbacks that were not drained, so cleanup still
        # happens when another thread's close() timed out while waiting.
        self._resource_scope.close()


