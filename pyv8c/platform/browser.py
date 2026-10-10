"""Browser and browser context: the lifecycle that owns a Page."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import pyv8  # patch target for native Context instrumentation

from ..engine.bindings import Idl, Bindings
from ..foundation.environment import BrowserEnvironment
from ..foundation.host_errors import HostStateError, LoadFailure
from ..foundation.urls import origin_string, origin_tuple
from ..network.assembly import assemble_backend
from ..network.cookies import CookieJar
from ..network.proxy import normalize_proxy
from ..network.registry import HttpTransport, WebSocketTransport
from .bootstrap import bootstrap_runtime
from .page import Page

#: With ``threaded_loaders``, loader and interceptor callbacks run here instead of on
#: the page's owner thread, or a slow one would freeze the realm.
_LOADER_WORKERS = 4


class BrowserContext:
    """A browsing session: its own cookies, blob URLs and network stack.

    Pages created here share that state, so a document, the ``fetch`` calls it
    makes and a ``page.net`` request are one session as far as a server is
    concerned; ``context.pages`` lists the pages still open and
    ``context.cookies`` is the jar behind them.  Contexts of one browser share
    nothing else -- cookies, cached responses and engines are per context --
    except for a disk cache directory when one was configured.
    """

    def __init__(self, browser, *, proxy=None, trace=None, traceable=None, trace_callback=None):
        self.browser, self.pages = browser, []
        self.cookies = CookieJar()
        #: Its own proxy setting, or the browser's while it was given none.
        self.proxy = browser.proxy if proxy is None else proxy
        #: Trace settings for this context alone; ``None`` keeps the browser's.
        self.trace = browser.trace if trace is None else bool(trace)
        self.traceable = (browser.traceable if traceable is None else bool(traceable)) or self.trace
        self.trace_callback = browser.trace_callback if trace_callback is None else trace_callback
        self.resource_cache_dir = browser.cache_dir if browser.cache_enabled else None
        self.resource_cache_lock = threading.RLock()
        self.network_backend = assemble_backend(browser, proxy=self.proxy)
        if self.network_backend is not None:
            self.network_backend.cookies = self.cookies
        self._blob_urls = {}
        self._blob_origins = {}
        self._databases = {}
        self.closed = False
        self._devtools = None

    def _ensure_open(self):
        if self.closed or self.browser._closed:
            raise HostStateError("BrowserContext is closed")

    def create_blob_url(self, blob, realm=None):
        """Mint a ``blob:`` URL whose origin is the creating realm's origin.

        The blob inherits the *creator* origin rather than one derived from the
        serialized URL: two opaque origins must stay distinct, so a fresh
        ``origin_tuple`` would make ``blob:null/...`` documents look unrelated
        to the page that created them.
        """
        self._ensure_open()
        url_source = "about:blank" if realm is None else getattr(realm, "url", "about:blank")
        url = "blob:" + origin_string(url_source) + "/" + str(uuid.uuid4())
        self._blob_urls[url] = (bytes(blob._data), blob.get_type())
        creator_origin = None if realm is None else getattr(realm, "origin", None)
        self._blob_origins[url] = creator_origin if creator_origin is not None else origin_tuple(url_source)
        return url

    def resolve_blob_url(self, url):
        self._ensure_open()
        if url not in self._blob_urls:
            raise LoadFailure("revoked or unknown Blob URL")
        return self._blob_urls[url]

    def revoke_blob_url(self, url):
        self._ensure_open()
        self._blob_urls.pop(url, None)
        self._blob_origins.pop(url, None)

    # Context-level services exposed to realm objects without exposing storage.
    @property
    def cookie_jar(self):
        return self.cookies

    def new_page(self,
                 url: str = "about:blank",
                 *,
                 trace: bool | None = None,
                 environment: dict | BrowserEnvironment | None = None) -> Page:
        """Create a page in this context and return it.

        A URL other than ``about:blank`` is loaded right away with
        :meth:`Page.goto <pyv8c.platform.page.Page.goto>`; when that load fails
        the new page is closed again and the error propagates, so a returned page
        is always usable.

        :param url: initial URL; the default creates an empty document without
            touching the loader.
        :param trace: start this page traced; ``None`` inherits the Browser
            setting.
        :param environment: keys overlaying the Browser environment for this page
            only.
        :returns: the new :class:`Page <pyv8c.platform.page.Page>`, appended to
            :attr:`pages`.
        :raises InvalidStateError: the context or its browser is closed.
        """
        self._ensure_open()
        page = Page(self, trace=trace, environment=environment)
        self.pages.append(page)
        if str(url) != "about:blank":
            try:
                page.goto(url)
            except BaseException:
                page.close()
                raise
        return page

    def _discard_page(self, page):
        try:
            self.pages.remove(page)
        except ValueError:
            pass

    def start_devtools(self,
                       page: Page,
                       *,
                       options: pyv8.DevToolsOptions | None = None) -> pyv8.DevToolsHandle:
        """Attach a DevTools server to *page* and return its handle.

        One server per context, driving a single debugger client; the supported
        protocol domains and the startup handshake are in ``docs/DEVTOOLS.md``.

        :param page: a page of this context that is still open.
        :param options: :class:`pyv8.DevToolsOptions`; the defaults bind
            ``127.0.0.1`` on a system-chosen port and pause once, at the next
            host statement after the handshake.
        :returns: the handle -- ``url``, ``wait_for_connection``,
            ``wait_for_ready``, ``close``, and a context manager.
        :raises InvalidStateError: the context is closed, the page belongs to
            another context or is closed, or a server is already attached.
        """
        self._ensure_open()
        if page not in self.pages or page.closed:
            raise HostStateError("Page does not belong to this BrowserContext")
        if self._devtools is not None:
            raise HostStateError("BrowserContext already has DevTools attached")
        from .devtools import start_devtools
        return start_devtools(self, page, options=options)

    def close(self) -> None:
        """Close the context: its pages, then DevTools and the network stack.

        Best effort and idempotent: each part is close-attempted even when an
        earlier one raised, blob URLs are dropped, and the first error is
        re-raised once the rest was attempted.
        """
        if self.closed:
            return
        first_error = None
        for page in list(self.pages):
            try:
                page.close()
            except BaseException as exc:
                # Teardown is best effort: one realm's error must not leave the
                # later pages, DevTools or the network backend alive.
                if first_error is None:
                    first_error = exc
        for name in ("_devtools", "network_backend"):
            resource = getattr(self, name)
            if resource is not None:
                try:
                    resource.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    setattr(self, name, None)
        self._blob_urls.clear()
        self._blob_origins.clear()
        self._databases.clear()
        if first_error is not None:
            raise first_error
        self.closed = True
        if self in self.browser.contexts:
            self.browser.contexts.remove(self)


class Browser:
    """A browser: the configuration every context and page starts from.

    One instance owns the native runtime, the interface baseline and the
    environment its contexts derive from.  :meth:`new_context` creates an
    isolated session and :meth:`close` closes every session it handed out; the
    engines a context needs are built when that context is created.

    JS objects belong to the thread that owns their realm (see ``docs/USAGE.md``).
    """

    def __init__(self, *,
                 environment: dict | BrowserEnvironment | None = None,
                 real_network: bool = True,
                 resource_loader: Callable | None = None,
                 resource_interceptor: Callable | None = None,
                 threaded_loaders: bool = False,
                 http_engine: Literal["cronet", "curl_cffi"] | HttpTransport | Callable | None = "curl_cffi",
                 http_options: dict | None = None,
                 ws_engine: Literal["cronet", "websockets"] | WebSocketTransport | Callable | None = "websockets",
                 ws_options: dict | None = None,
                 proxy: str | None = None,
                 default_headers: dict | None = None,
                 traceable: bool = False,
                 trace: bool = False,
                 trace_callback: Callable | None = None,
                 cache: bool = True,
                 cache_dir: str | os.PathLike | None = None,
                 expose_unimplemented: bool = True,
                 stub_logging: Literal["on", "verbose", "off"] = "off",
                 interface_overrides: dict | None = None,
                 idl_path: str | os.PathLike | None = None,
                 baseline_mismatch: Literal["error", "warn"] = "warn"):
        """Configure one browser.

        Every value is read here: native templates and the interface baseline are
        assembled in this call and are not reconfigured later, so another baseline
        means another Browser.

        :param environment: flat "dotted key" dict or
            :class:`~pyv8c.foundation.environment.BrowserEnvironment`; keys left
            out come from the bundled Chrome profile.  Contexts and pages may
            overlay it.
        :param real_network: assemble ``http_engine`` and ``ws_engine``; ``False``
            keeps this browser offline, and a ready engine handed to either of them
            is then refused rather than dropped.  The default engines come from the
            ``[network]`` extra.
        :param resource_loader: synchronous ``loader(url, request) -> Response``
            answering every load this browser performs; what it answers never
            reaches a transport.  The offline way to serve a page.
        :param resource_interceptor: ``interceptor(url, request)`` consulted
            before each load: ``None``/``True`` loads normally, ``False`` answers
            with an empty 200, anything else (``Response``, dict, str) is the
            response.
        :param threaded_loaders: run ``resource_loader`` and
            ``resource_interceptor`` on a bounded worker pool instead of the page's
            owner thread, so a callback blocked on its own I/O does not freeze the
            realm.  A callback running there may not touch the page, ``page.evaluate``
            and this host's own cache included.
        :param http_engine: the HTTP engine -- a registered name (built with
            ``http_options``), a ready object with ``request_sync``, or a factory
            called once per context; ``None`` leaves the role empty.  Further
            engines can be added with
            :func:`~pyv8c.network.registry.register_transport`.
        :param http_options: options handed to the HTTP engine; a key it does not
            accept is refused when a context is created rather than reaching it.
        :param ws_engine: the WebSocket engine -- a registered name (built with
            ``ws_options``), a ready object with ``websocket``, or a factory called
            once per context; ``None`` leaves the role empty.  Further engines can
            be added with
            :func:`~pyv8c.network.registry.register_ws_transport`.
        :param ws_options: options handed to the WebSocket engine, same rule.
        :param proxy: the ``http://`` or ``https://`` proxy every request and
            WebSocket handshake of this browser's contexts goes through, with
            any credentials in the URL (``http://user:pass@host:port``).  Left
            out, traffic goes directly even when the environment names a proxy;
            ``""`` asks for the same thing explicitly.  A context may override
            it with :meth:`new_context`, and SOCKS is not supported.
        :param default_headers: headers every ``page.net`` request starts from.
        :param trace: start each page with tracing on.
        :param trace_callback: receives trace events instead of Loguru.
        :param traceable: allow a page to switch tracing on later (see
            :attr:`Page.trace <pyv8c.platform.page.Page.trace>`); ``trace=True``
            implies it.
        :param cache: enable the per-context resource cache.
        :param cache_dir: keep that cache on disk under this directory; without
            it responses are cached in memory only.
        :param expose_unimplemented: let the exposure plan include baseline
            interfaces with no hand-written implementation (they answer with
            stubs).
        :param stub_logging: ``"off"`` (the default) reports nothing, ``"on"``
            reports each unimplemented member once, and ``"verbose"`` also reports
            the placeholder handlers the baseline required.
        :param interface_overrides: hooks that answer members of the baseline
            instead of their implementation, as
            ``{"InterfaceName": {"memberName": hook}}``.  A hook is a plain function
            that receives what the member's position passes -- ``(receiver, *args)``
            for a prototype or instance method, ``(*args)`` for one on the interface
            itself, ``(receiver)`` for an attribute read and ``(receiver, value)``
            for a write -- and what it returns is what the script sees.  An entry may
            name the slot instead, as ``{"get": hook, "set": hook}``; a write hook
            also makes the member writable.  An unknown interface, an unknown member,
            a slot that member cannot answer, a write hook for a member the baseline
            calls read-only, a bound method and anything not callable are refused
            here.  Constructors, constants and the named/indexed handlers are not
            covered.
        :param idl_path: Chromium interface baseline JSON or ZIP to
            use instead of the bundled one.
        :param baseline_mismatch: ``"warn"`` (the default) logs a difference
            between hand-written implementations and the chosen baseline once and
            skips it, which is how another Chromium version's baseline is run;
            ``"error"`` fails at assembly instead, which is what keeps a mistake in
            one's own implementation from passing silently.
        """
        if trace_callback is not None and not callable(trace_callback):
            raise TypeError("trace_callback must be callable or None")
        self.trace_callback = trace_callback
        started = time.perf_counter_ns()
        self._closed = False
        # pyv8 starts its global V8 runtime on first use, not at import, so the
        # first realm created below carries that cost.
        self.contexts = []
        self.resource_loader = resource_loader
        self.resource_interceptor = resource_interceptor
        self.threaded_loaders = bool(threaded_loaders)
        self._loader_pool = None
        self._loader_lock = threading.Lock()
        self.cache_enabled = bool(cache)
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else None
        #: ``False`` assembles no engines; a ready engine is then refused.
        self.real_network = bool(real_network)
        self.http_engine = http_engine
        self.ws_engine = ws_engine
        #: Options for the two engines; a key an engine does not accept is refused
        #: when a context is created rather than passed on unread.
        self.http_options = dict(http_options or {})
        self.ws_options = dict(ws_options or {})
        #: The proxy contexts start from: ``None`` is direct, ``""`` asks for it.
        self.proxy = normalize_proxy(proxy)
        #: Headers every host request starts from, on top of the environment ones.
        self.default_headers = dict(default_headers or {})
        self.trace = bool(trace)
        self.traceable = bool(traceable) or self.trace
        self.environment = BrowserEnvironment.from_dict(environment)
        self.expose_unimplemented = bool(expose_unimplemented)
        self.stub_logging = str(stub_logging or "off")
        self._startup_stats = {}
        snapshot = Idl.load(idl_path)
        loaded = time.perf_counter_ns()
        self.bindings = Bindings(
            snapshot, runtime=bootstrap_runtime(),
            stub_logging=self.stub_logging,
            expose_unimplemented=self.expose_unimplemented,
            baseline_mismatch=baseline_mismatch,
            interface_overrides=interface_overrides,
        )
        validation_finished = validation_started = loaded
        self.idl_path = snapshot.path
        self.idl_info = snapshot.info
        self._startup_stats.update(Idl._last_timings)
        plan = self.bindings.plan("Window")
        self._startup_stats.update({
            "validation_ms": (validation_finished - validation_started) / 1_000_000,
            "browser_init_ms": (validation_finished - started) / 1_000_000,
            "known_interfaces": len(plan.installs),
            "core_interfaces": len(plan.eager),
            "startup_interfaces": len(plan.ready),
            "deferred_interfaces": len(plan.lazy),
            "materialized_interfaces": 0,
            "binding_binder_ms": 0.0,
            "context_create_ms": 0.0,
            "deferred_global_declare_ms": 0.0,
            "deferred_globals": 0,
            "deferred_materialized": 0,
            "baseline_discrepancies": len(self.bindings.discrepancies),
        })

    @property
    def startup_stats(self) -> MappingProxyType:
        """Read-only snapshot of this Browser's startup timings and counts.

        Baseline read/cache/compile/decode, validation and browser-init
        milliseconds, context-creation cost, and how many interfaces the Window
        exposure plan knows, materialized or still deferred.  Set
        ``PYV8C_STARTUP_STATS=0`` to record nothing.
        """
        return MappingProxyType(dict(self._startup_stats))

    def _record_startup(self, **values):
        if os.environ.get("PYV8C_STARTUP_STATS", "1") != "0":
            self._startup_stats.update(values)

    def new_context(self, *, proxy: str | None = None, trace: bool | None = None,
                    traceable: bool | None = None,
                    trace_callback: Callable | None = None) -> BrowserContext:
        """Create an isolated session: its own cookies, blobs and network stack.

        :param proxy: where this context's traffic goes, overriding
            :attr:`proxy` for it alone: left out it keeps the browser's setting,
            a URL sends it through that proxy instead, and ``""`` sends it
            directly even when the browser has one.  Contexts of one browser may
            therefore leave by different exits at the same time.
        :param trace: report trace events for this context alone; ``None`` keeps
            the browser setting.
        :param traceable: let its pages switch tracing on later, same defaulting.
        :param trace_callback: receive this context's events instead of the
            browser's callback.
        :returns: the new :class:`BrowserContext`, appended to :attr:`contexts`.
        :raises InvalidStateError: the browser is closed.
        :raises ValueError: the proxy value is not an ``http``/``https`` URL.
        """
        if self._closed:
            raise HostStateError("Browser is closed")
        if trace_callback is not None and not callable(trace_callback):
            raise TypeError("trace_callback must be callable or None")
        context = BrowserContext(self, proxy=None if proxy is None else normalize_proxy(proxy),
                                 trace=trace, traceable=traceable, trace_callback=trace_callback)
        self.contexts.append(context)
        return context

    @property
    def loader_pool(self):
        """The bounded pool the page's loader callbacks run on, created on first use."""
        pool = self._loader_pool
        if pool is None:
            with self._loader_lock:
                if self._loader_pool is None:
                    self._loader_pool = ThreadPoolExecutor(
                        max_workers=_LOADER_WORKERS, thread_name_prefix="pyv8c-loader")
                pool = self._loader_pool
        return pool

    def close(self) -> None:
        """Close every context, then mark the browser closed.

        Best effort and idempotent: every context gets its close attempt even
        when an earlier one raised, and the first error is re-raised afterwards.
        A closed browser refuses new contexts.
        """
        if self._closed:
            return
        # Contexts close first: a page that is already closed is what makes its queued
        # loads stand down quietly.  The pool going away first was the other order, and
        # every load still queued then failed into a script's error event -- a burst of
        # warnings about a shutdown, on a page that was on its way out anyway.
        first_error = None
        for context in list(self.contexts):
            try:
                context.close()
            except BaseException as exc:  # every context still gets its close attempt
                if first_error is None:
                    first_error = exc
        pool = self._loader_pool
        if pool is not None:
            # Loader work is not the browser's to wait for, and a callback may be
            # blocked on its own I/O.
            pool.shutdown(wait=False, cancel_futures=True)
        if first_error is not None:
            raise first_error
        self._closed = True
