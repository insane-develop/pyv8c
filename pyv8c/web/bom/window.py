"""Window, its named-properties prototype and native cross-origin policy hooks."""
import pyv8


from ...engine.features import LazyValueStore
from ...idl import interface
from ...foundation.arguments import (
    MISSING,
    dictionary,
    dom_string,
    event_handler,
    is_nullish,
    missing_error,
    usv_string,
)
from ...foundation.base64 import forgiving_base64_decode, forgiving_base64_encode
from ...foundation.environment import BrowserEnvironment
from ..page_errors import DOMException, InvalidCharacterError, LegacyPropertyError, SecurityError, PageTypeError
from ..page_errors import js_error_value
from ...foundation.urls import origin_tuple, serialize_origin
from ..dom.custom_elements import CustomElementRegistry
from ..dom.events import EventTarget
from ..fetch.cache import CacheStorage
from ..fetch.request import Request
from ..fetch.response import Response
from .accessories import (
    CrashReportContext,
    External,
    LaunchQueue,
    Scheduler,
    SpeechSynthesis,
    StyleMedia,
    Viewport,
)
from .cookies import CookieStore
from .crypto import Crypto
from .media import MediaQueryList
from .navigation import Navigation
from .trusted_types import TrustedTypePolicyFactory
from .visual_viewport import VisualViewport
from .performance import Performance
from .scheduling import _SchedulingMixin
from .utils import deny_cross_origin, is_secure_context, window_secure_context

__all__ = ["BarProp", "Window", "WindowProperties"]


#: The two ways V8 words a failed string conversion, which a method prefixes.
_CONVERSION_FAILURES = frozenset({
    "Cannot convert a Symbol value to a string",
    "Cannot convert object to primitive value",
})


def _string_argument(value, member, interface_name="Window"):
    """A ``DOMString`` argument worded as WebIDL words it; an exception a
    caller's own ``toString`` threw is passed through untouched.
    """
    try:
        return dom_string(value)
    except pyv8.JSException as error:
        original = getattr(error, "original", None)
        reason = str(original._get("message")) if isinstance(original, pyv8.JSObject) else ""
        if reason not in _CONVERSION_FAILURES:
            raise
        raise PageTypeError(f"Failed to execute '{member}' on '{interface_name}': {reason}") from None


def base64_like(interface_name, member, data):
    """The shared WindowOrWorkerGlobalScope base64 algorithms."""
    if data is MISSING:
        raise missing_error(interface_name, member, 1, 0, constructor=False)
    text = _string_argument(data, member, interface_name)
    action = "decoded" if member == "atob" else "encoded"
    prefix = f"Failed to execute '{member}' on '{interface_name}': "
    if any(ord(character) > 0xFF for character in text):
        raise InvalidCharacterError(prefix + f"The string to be {action} contains"
                                    " characters outside of the Latin1 range.")
    if member == "btoa":
        return forgiving_base64_encode(text)
    decoded = forgiving_base64_decode(text)
    if decoded is None:
        raise InvalidCharacterError(prefix + "The string to be decoded is not correctly encoded.")
    return decoded


#: Names WindowProxy exposes to a cross-origin caller; the table lives here for both.
_PROXY_SAFE_NAMES = frozenset((
    "window", "self", "top", "parent", "opener", "frames", "length", "closed",
    "location", "close", "focus", "blur", "postMessage",
))


@interface('WindowProperties', 'EventTarget')
class WindowProperties(EventTarget):
    """The named-properties object between Window and EventTarget prototypes.

    Its callbacks run against the owning Window and use a separate callback
    family from Window's own named interceptors.
    """
    __idl_overrides__ = {
        "interface": {
            #: This is the object a page's bare names resolve through.
            "named_properties_object": True,
            "length": 0,
        }
    }

    _NAMED_TAGS = frozenset(("embed", "form", "img", "object"))

    def _named_elements(self, name):
        document = self._services.document_services.document
        return [node for node in document.fn_querySelectorAll("*")
                if node.attributes.get("id") == name or (
                    node.localName in self._NAMED_TAGS and node.attributes.get("name") == name)]

    def __v8_property_name_get__(self, name):
        if not isinstance(name, str) or not name:
            return None
        self._require_same_origin()
        document = self._services.document_services.document
        # Child browsing-context names take precedence over matching elements.
        for frame in document.fn_querySelectorAll("iframe"):
            if frame.attributes.get("name") == name and frame._child_services is not None:
                return Window._accessor_window(self)._view(frame._child_services.window)
        matches = self._named_elements(name)
        if not matches:
            return None
        if len(matches) == 1:
            return self._services.realm_services.object_handle(matches[0])
        from ..dom.collections import HTMLCollection
        collection = HTMLCollection(owner=document, getter=lambda: self._named_elements(name))
        return self._services.realm_services.object_handle(collection)

    def __v8_property_name_query__(self, name):
        return 0 if self.__v8_property_name_get__(name) is not None else None

    def __v8_property_name_enum__(self):
        # Named-properties objects have no own keys, despite synthesized
        # [[GetOwnProperty]] descriptors for supported names.
        return []


@interface('BarProp', None)
class BarProp:
    """One of a window's bars: only ``visible`` is observable, and always true."""

    def __init__(self, name="", visible=True, **_):
        self._name, self._visible = str(name), bool(visible)

    def get_visible(self):
        return self._visible


class _WindowFeatureValues(LazyValueStore):
    """The Window's feature slots, built by their Feature on first access.

    A slot name routes to :meth:`FeatureRuntime.create_window_value` and the
    result is cached, so construction stays in the Feature and identity per Window.
    """

    __slots__ = ("_runtime", "_window")

    def __init__(self, runtime, window):
        super().__init__()
        self._runtime, self._window = runtime, window

    def _create_value(self, slot):
        return self._runtime.create_window_value(slot, self._window)


#: The window's own bar properties, one name each.
BAR_NAMES = ("locationbar", "menubar", "toolbar", "statusbar", "scrollbars", "personalbar")


def _singleton_values(window):
    """The ``[SameObject]`` objects a page reads off the window itself."""
    def bar(name):
        return lambda: BarProp(name)

    return LazyValueStore({
        "performance": lambda: Performance(services=window._services),
        "crypto": lambda: Crypto(window_secure_context(window),
                                 services=window._services),
        "caches": lambda: CacheStorage(services=window._services),
        "customElements": lambda: CustomElementRegistry(services=window._services),
        "visualViewport": lambda: VisualViewport(window=window),
        "viewport": Viewport,
        "external": External,
        "crashReport": CrashReportContext,
        "launchQueue": LaunchQueue,
        "speechSynthesis": lambda: SpeechSynthesis(services=window._services),
        "styleMedia": lambda: StyleMedia(window),
        "scheduler": lambda: Scheduler(services=window._services),
        "cookieStore": lambda: CookieStore(services=window._services),
        "navigation": lambda: Navigation(window=window),
        "trustedTypes": TrustedTypePolicyFactory,
        **{name: bar(name) for name in BAR_NAMES},
    })


def _fetch_failure(error):
    """What a page's ``fetch`` rejects with: a bare ``TypeError``, as Chrome answers.

    A load the page cannot have -- a scheme it may not use, a CORS answer it may not
    read, a transport that failed -- is the host's own outcome, so the page hears what a
    browser tells it and the reason stays in the log.
    """
    from ...foundation.error_policy import is_host_failure

    if is_host_failure(error) or (isinstance(error, DOMException)
                                  and error.get_name() == "NetworkError"):
        from loguru import logger

        # The reason stays in the log; the page hears only Chrome's wording.
        logger.debug("fetch failed: {}", error)
        return PageTypeError("Failed to fetch")
    return error


def fetch_like(services, interface_name, request, options, resources=None, initiator_url=None):
    """``fetch`` as a window and a worker global both run it: a promise either way.

    The two globals differ only in the interface they name in an argument error, so the steps
    live here rather than in either of them -- a worker whose ``fetch`` were missing entirely
    is a realm the page cannot be told about, which is what it used to be.

    *resources* is where the load is handed to: a worker's realm services carry no resource
    capability (the worker fetches through the page that owns it, as every other load does).
    *initiator_url* is that worker's own URL, so the request the load builds is attributed to
    the realm that asked rather than to the page that owns it.
    """
    realm = services.realm_services
    loader = (resources or services).resource_services
    promise, resolve, reject = realm.make_promise()
    try:
        if request is MISSING:
            raise missing_error(interface_name, "fetch", 1, 0)
        if not isinstance(request, Request):
            request = usv_string(request)
        options = dictionary(
            options, name="RequestInit",
            prefix=f"Failed to execute 'fetch' on '{interface_name}': ")
        req = Request(request, options, services=services)
    except BaseException as exc:
        realm.post_task(lambda exc=exc: reject(js_error_value(exc)))
        return promise
    def deliver(response, error):
        """Hand the answer to the realm that asked, which is not always the one that built it.

        A response carries the realm services of whoever built it, and a worker reads its own
        isolate: a value built there comes back as "belongs to another isolate".  The body
        crosses as bytes, and the object is rebuilt where it is read.
        """
        if error is not None:
            reject(js_error_value(_fetch_failure(error)))
            return
        if services is not getattr(response, "_services", None):
            try:
                response = Response(response.body, {
                    "headers": response.headers, "status": response.status,
                    "statusText": response.status_text, "url": response.url,
                    "redirected": response.redirected, "type": response.type,
                }, services=services)
            except BaseException as exc:  # a rebuild that fails must be seen, not swallowed
                reject(js_error_value(exc))
                return
        resolve(response)

    handle = loader.load_resource_async(req.get_url(), {
        "method": req.get_method(), "headers": req.get_headers(), "body": req._body,
        "timeout": req._timeout, "credentials": req._credentials, "mode": req._mode,
        "redirect": req._redirect, "resource_type": "fetch",
        "initiator_url": initiator_url,
    }, deliver)
    #: A fetch the caller aborted: drop the load and reject with the reason.
    def abort():
        handle.cancel()
        reason = req._signal._reason
        if isinstance(reason, BaseException):
            reason = js_error_value(reason)
        realm.post_task(lambda reason=reason: reject(reason))

    if req._signal is not None:
        req._signal._subscribe(abort)
    return promise


@interface('Window', 'WindowProperties')
class Window(_SchedulingMixin, WindowProperties):
    __idl_overrides__ = {
        "interface": {
            #: A window is the global object itself, which its own ``self`` reads back.
            "global_this": True,
            "cross_origin_check": "__v8_cross_origin_check__",
            "cross_origin_denied": "__v8_cross_origin_denied__",
            "cross_origin_index_del": "__v8_cross_origin_index_del__",
            "cross_origin_index_get": "__v8_cross_origin_index_get__",
            "cross_origin_index_set": "__v8_cross_origin_index_set__",
            "cross_origin_name_del": "__v8_cross_origin_name_del__",
            "cross_origin_name_enum": "__v8_cross_origin_name_enum__",
            "cross_origin_name_get": "__v8_cross_origin_name_get__",
            "cross_origin_name_query": "__v8_cross_origin_name_query__",
            "cross_origin_name_set": "__v8_cross_origin_name_set__",
            "index_del": "__v8_index_del__",
            "index_enum": "__v8_index_enum__",
            "index_get": "__v8_index_get__",
            "index_set": "__v8_index_set__",
            "length": 0
        }
    }
    # Ordinary members use Web IDL bindings; dynamic names live on
    # WindowProperties, not a generic Python interceptor on Window.
    _context_origins = {}
    def __init__(self, services=None, feature_runtime=None, **kw):
        super().__init__(services=services, **kw); self._services = services
        self._origin = getattr(services, "_origin", ("opaque", object()))
        self._proxies = {}
        #: Set for a window a frame owns; the element is what survives navigation.
        self._frame_element = None
        environment = getattr(getattr(services, "realm_services", None), "environment", None)
        viewport = BrowserEnvironment.from_dict(environment).viewport
        if feature_runtime is None:
            raise RuntimeError("Window requires the Browser FeatureRuntime")
        self._features = _WindowFeatureValues(feature_runtime, self)
        self._window_values = _singleton_values(self)
        self.location = self._features.value("location")
        self.innerWidth, self.innerHeight = viewport.width, viewport.height
        #: A mobile viewport has no window decorations around it.
        inset = (0, 0) if viewport.mobile else (viewport.outer_inset_width,
                                                viewport.outer_inset_height)
        self.outerWidth = viewport.width + inset[0]
        self.outerHeight = viewport.height + inset[1]
        self.devicePixelRatio = viewport.device_scale_factor

    @property
    def navigator(self):
        return self._features.value("navigator")

    @property
    def screen(self):
        return self._features.value("screen")

    @property
    def localStorage(self):
        return self._features.value("localStorage")

    @property
    def sessionStorage(self):
        return self._features.value("sessionStorage")

    @property
    def history(self):
        return self._features.value("history")

    @property
    def indexedDB(self):
        return self._features.value("indexedDB")

    def sync_history_url(self, url):
        """Re-point the current history entry at a committed document URL.

        A navigation replaces the entry rather than pushing one, and a page that
        never reads ``history`` does not materialize one here.
        """
        if "history" in self._features.cached_names:
            self.history.replace_same_document(str(url))

    def __v8_cross_origin_check__(self, accessor):
        return accessor is self or getattr(accessor, "_origin", None) == self._origin

    def __v8_cross_origin_denied__(self):
        deny_cross_origin()

    def _cross_origin_proxy(self):
        entry = self._context_origins.get(id(pyv8.current_context()))
        if entry is None:
            raise SecurityError("The accessing Window is not available")
        return entry[0]._proxy_for(self)

    def __v8_cross_origin_name_get__(self, name):
        proxy = self._cross_origin_proxy()
        if name in _PROXY_SAFE_NAMES:
            getter = getattr(proxy, "get_" + name, None)
            return getter() if getter else getattr(proxy, "fn_" + name)
        return proxy.__v8_name_get__(name)

    def __v8_cross_origin_name_set__(self, name, value):
        return self._cross_origin_proxy().__v8_name_set__(name, value)

    def __v8_cross_origin_name_query__(self, name):
        return self._cross_origin_proxy().__v8_name_query__(name)

    def __v8_cross_origin_name_del__(self, name):
        return self._cross_origin_proxy().__v8_name_del__(name)

    def __v8_cross_origin_name_enum__(self):
        return list(_PROXY_SAFE_NAMES)

    def __v8_cross_origin_index_get__(self, index):
        return self._cross_origin_proxy().__v8_index_get__(index)

    def __v8_cross_origin_index_set__(self, index, value):
        return self._cross_origin_proxy().__v8_index_set__(index, value)

    def __v8_cross_origin_index_del__(self, index):
        return self._cross_origin_proxy().__v8_index_del__(index)

    @classmethod
    def _bind_context(cls, context, window):
        cls._context_origins[id(context)] = (window, window._origin)
        window._ctx = context

    @classmethod
    def _unbind_context(cls, context):
        cls._context_origins.pop(id(context), None)

    @classmethod
    def _accessor_window(cls, fallback=None):
        entry = cls._context_origins.get(id(pyv8.current_context()))
        return entry[0] if entry is not None else fallback

    def _accessor_origin(self):
        current = pyv8.current_context()
        entry = self._context_origins.get(id(current))
        return entry[0]._origin if entry is not None else self._origin

    def _same_origin(self):
        return self._accessor_origin() == self._origin

    def _require_same_origin(self):
        if not self._same_origin():
            deny_cross_origin()

    def get_window(self): return self
    def get_self(self): return self
    def get_parent(self): return self._view(self._parent_target())
    def get_top(self): return self._view(self._top_target())

    def _parent_target(self):
        parent = self._services.parent_window()
        return self if parent is None else parent

    def _top_target(self):
        top = self._services.top_window()
        return self if top is None else top

    def _view(self, target):
        if target is self:
            return self
        return target._services.realm_services.window_handle()

    def _proxy_for(self, target):
        # One proxy per browsing context, not per Window: a navigation replaces
        # the Window while the reference a page holds stays the same object.
        frame = getattr(target, "_frame_element", None)
        key = frame if frame is not None else target._services
        proxy = self._proxies.get(key)
        if proxy is None:
            from .window_proxy import WindowProxy
            proxy = self._proxies[key] = WindowProxy(self, target)
        return proxy

    def __v8_index_get__(self, index):
        frames = self._frame_windows()
        if 0 <= index < len(frames):
            return self._view(frames[index])
        return None

    def __v8_index_set__(self, index, value):
        """A slot holds a live window: nothing may be assigned over it."""
        raise LegacyPropertyError.indexed(
            "set", index, "Window", "Indexed property setter is not supported."
        )

    def __v8_index_del__(self, index):
        """A frame slot refuses deletion; a slot holding nothing is already gone."""
        return not 0 <= index < len(self._frame_windows())

    def __v8_index_enum__(self):
        """The child frames, in the order their slots are indexed."""
        return len(self._frame_windows())

    def _frame_windows(self):
        return tuple(node._child_services.window
                     for node in self._services.document_services.document.fn_querySelectorAll("iframe")
                     if node._child_services is not None)

    def get_document(self): self._require_same_origin(); return self._services.document_services.document
    def get_frames(self): return self
    def get_length(self): return len(self._frame_windows())
    def get_closed(self): return bool(getattr(self._services, "closed", False))
    def get_opener(self): return pyv8.Null
    def get_onmessage(self): self._require_same_origin(); return self._get_handler("onmessage")
    def set_onmessage(self, value):
        if not is_nullish(value):
            value = event_handler(value)
        self._require_same_origin()
        self._set_handler('onmessage', value)
    def get_location(self):
        # A caller may retain this reference after the child realm navigates.
        # Keep its host binding in the incumbent realm, as the former facade did.
        context = pyv8.incumbent_context()
        if context is None:
            return self.location
        context.to_js(self.location)
        return context.js_obj(self.location)
    def set_location(self, value): self.location.set_href(value)
    def get_navigator(self): self._require_same_origin(); return self.navigator
    def get_clientInformation(self): self._require_same_origin(); return self.navigator
    def get_screen(self): self._require_same_origin(); return self.screen
    def get_localStorage(self): self._require_same_origin(); return self.localStorage
    def get_sessionStorage(self): self._require_same_origin(); return self.sessionStorage
    def get_history(self): self._require_same_origin(); return self.history
    def get_indexedDB(self): self._require_same_origin(); return self.indexedDB
    def get_innerWidth(self): self._require_same_origin(); return self.innerWidth
    def get_innerHeight(self): self._require_same_origin(); return self.innerHeight
    def get_outerWidth(self): self._require_same_origin(); return self.outerWidth
    def get_outerHeight(self): self._require_same_origin(); return self.outerHeight
    def get_devicePixelRatio(self): self._require_same_origin(); return self.devicePixelRatio
    def get_offscreenBuffering(self):
        """Legacy; a browser answers true."""
        return True
    def get_name(self):
        """The name a page gave this window; empty until it sets one."""
        return str(getattr(self, "_window_name", "") or "")
    def set_name(self, value):
        self._require_same_origin()
        self._window_name = str(value)
    def _publish_name(self):
        """Assign the name over the one a fresh global object already carries."""
        self._services.realm_services.window_property("set", "name", self.get_name())
    def _window_value(self, name):
        """The one object this window hands out for a ``[SameObject]`` member."""
        return self._window_values.value(name)

    def get_origin(self):
        self._require_same_origin()
        return self.location.get_origin()

    def get_scrollX(self):
        """How far the view has scrolled: nothing here scrolls one."""
        self._require_same_origin()
        return 0

    def get_scrollY(self): self._require_same_origin(); return 0
    def get_pageXOffset(self): return self.get_scrollX()
    def get_pageYOffset(self): return self.get_scrollY()

    def get_screenX(self):
        """Where the window sits on the screen: the top-left corner here."""
        self._require_same_origin()
        return 0

    def get_screenY(self): self._require_same_origin(); return 0
    def get_screenLeft(self): return self.get_screenX()
    def get_screenTop(self): return self.get_screenY()

    def get_isSecureContext(self):
        """Whether the document is in a secure context, the one that created it included.

        An ``about:blank`` or ``about:srcdoc`` document carries no scheme of its own, so a
        browser inherits the answer from its creator: an inline frame inside an ``https`` page
        is a secure context, and Chrome exposes ``navigator.userActivation`` and its thirty
        relatives there.  Judged by its own URL alone the frame would answer ``false``.
        """
        self._require_same_origin()
        return window_secure_context(self)

    def get_crossOriginIsolated(self):
        """Whether the document is cross-origin isolated: no COOP/COEP is set."""
        self._require_same_origin()
        return False

    def get_originAgentCluster(self): self._require_same_origin(); return False
    def get_credentialless(self): self._require_same_origin(); return False

    def get_status(self):
        """The window's status bar text, which starts empty and only a page sets."""
        self._require_same_origin()
        return getattr(self, "_status", "")

    def set_status(self, value):
        self._require_same_origin()
        self._status = dom_string(value)

    def get_frameElement(self):
        """The frame a child window lives in; a top-level window has none."""
        self._require_same_origin()
        parent = self._parent_target()
        if parent is self or parent._origin != self._accessor_origin():
            return pyv8.Null
        document = parent._services.document_services.document
        return next((frame for frame in document.fn_querySelectorAll("iframe")
                     if frame._child_services is not None
                     and frame._child_services.window is self), pyv8.Null)

    def get_performance(self):
        self._require_same_origin()
        return self._window_value("performance")

    def get_crypto(self):
        self._require_same_origin()
        return self._window_value("crypto")

    def get_customElements(self):
        self._require_same_origin()
        return self._window_value("customElements")

    def get_visualViewport(self):
        self._require_same_origin()
        return self._window_value("visualViewport")

    def get_viewport(self): self._require_same_origin(); return self._window_value("viewport")
    def get_external(self): self._require_same_origin(); return self._window_value("external")
    def get_crashReport(self): self._require_same_origin(); return self._window_value("crashReport")
    def get_launchQueue(self): self._require_same_origin(); return self._window_value("launchQueue")
    def get_speechSynthesis(self): self._require_same_origin(); return self._window_value("speechSynthesis")

    def get_styleMedia(self):
        self._require_same_origin()
        return self._window_value("styleMedia")

    def get_scheduler(self):
        self._require_same_origin()
        return self._window_value("scheduler")

    def get_navigation(self):
        self._require_same_origin()
        return self._window_value("navigation")

    def get_trustedTypes(self):
        self._require_same_origin()
        return self._window_value("trustedTypes")

    def get_fence(self):
        """An unfenced page has no fence, which Chrome reports as null."""
        self._require_same_origin()
        return pyv8.Null

    #: Chrome knows these names and answers undefined -- they gate on a secure
    #: context, or are gone -- so the value is the same and the log is quiet.
    def get_event(self): self._require_same_origin(); return pyv8.Undefined
    def get_caches(self):
        """Cache Storage is secure-context only, so an insecure page has none."""
        self._require_same_origin()
        if not window_secure_context(self):
            return pyv8.Undefined
        return self._window_value("caches")
    def get_cookieStore(self):
        self._require_same_origin()
        if not window_secure_context(self):
            return pyv8.Undefined
        return self._window_value("cookieStore")
    def get_documentPictureInPicture(self): self._require_same_origin(); return pyv8.Undefined

    def get_locationbar(self): self._require_same_origin(); return self._window_value("locationbar")
    def get_menubar(self): self._require_same_origin(); return self._window_value("menubar")
    def get_toolbar(self): self._require_same_origin(); return self._window_value("toolbar")
    def get_statusbar(self): self._require_same_origin(); return self._window_value("statusbar")
    def get_scrollbars(self): self._require_same_origin(); return self._window_value("scrollbars")
    def get_personalbar(self): self._require_same_origin(); return self._window_value("personalbar")

    def fn_matchMedia(self, query=MISSING, *_extra):
        """The query list for *query*, answered against this window's viewport."""
        if query is MISSING:
            raise missing_error('Window', 'matchMedia', 1, 0, constructor=False)
        self._require_same_origin()
        return MediaQueryList(dom_string(query), self.innerWidth, self.innerHeight)
    def fn_fetch(self, request=MISSING, options=None, *_extra):
        return fetch_like(self._services, "Window", request, options)

    def fn_atob(self, data=MISSING, *_extra):
        return base64_like("Window", "atob", data)

    def fn_btoa(self, data=MISSING, *_extra):
        return base64_like("Window", "btoa", data)
    def fn_structuredClone(self, value=MISSING, options=None, *_extra):
        from ..fetch.clone import clone_api
        return clone_api(self, "Window", value, options)

    def fn_close(self, *_extra): return None
    def fn_focus(self, *_extra): return None
    def fn_blur(self, *_extra): return None

    def fn_postMessage(self, message=MISSING, target_origin='/', transfer=MISSING, *_extra):
        if message is MISSING:
            raise missing_error('Window', 'postMessage', 1, 0, constructor=False)
        if target_origin is pyv8.Undefined:
            target_origin = '/'
        from ...foundation.arguments import sequence
        if transfer is not MISSING:
            target_origin = dom_string(target_origin)
            transfer = sequence(transfer)
        else:
            transfer = ()
        self._post_message(self, message, target_origin, transfer)

    def fn_webkitRequestFileSystem(self, type=MISSING, size=MISSING, success=MISSING, error=pyv8.Undefined, *_extra):
        if type is MISSING: raise missing_error("Window", "webkitRequestFileSystem", 4, 0, constructor=False)
        if size is MISSING: raise missing_error("Window", "webkitRequestFileSystem", 4, 1, constructor=False)
        if success is MISSING: raise missing_error("Window", "webkitRequestFileSystem", 4, 2, constructor=False)
        self._require_same_origin()
        # The API is an Optional Feature: loading it commits its interface group
        # to this realm before the first instance is handed back.
        self._services.realm_services.materialize_interface("DOMFileSystem")
        from .filesystem import request_file_system
        return request_file_system(self, type, size, success, error)

    def fn_getComputedStyle(self, element=MISSING, pseudo=pyv8.Undefined, *_extra):
        if element is MISSING:
            raise missing_error("Window", "getComputedStyle", 1, 0, constructor=False)
        from ..dom.html_elements import Element
        if not isinstance(element, Element):
            raise TypeError("The provided value is not an Element")
        from ..dom.layout import computed_style_object
        document = getattr(element, "ownerDocument", None)
        # Nothing is rendered in a tree no window owns, and CSSOM answers empty
        # values for one: that is what a document built by ``DOMParser`` is.
        rendered = document is None or document.get_defaultView() is not pyv8.Null
        return computed_style_object(element, rendered=rendered)

    def _post_message(self, source, message, target_origin="/", transfer=()):
        from ..page_errors import PageSyntaxError
        from ..fetch.clone import structured_clone, transfer_ports
        from ..fetch.events import MessageEvent

        # Null is an empty WindowPostMessageOptions dictionary.
        if target_origin is None or target_origin is pyv8.Null or target_origin is pyv8.Undefined:
            target_origin = {}
        if isinstance(target_origin, (dict, pyv8.JSObject)):
            from ...foundation.arguments import sequence
            options = dictionary(target_origin)
            target_origin = dom_string(options.get("targetOrigin", "/"))
            transfer = sequence(options.get("transfer", ()))
        target_origin = dom_string(target_origin)
        if target_origin == "/":
            expected_origin = source._origin
        elif target_origin == "*":
            expected_origin = None
        else:
            expected_origin = origin_tuple(target_origin)
            if expected_origin[0] == "opaque":
                raise PageSyntaxError("Invalid target origin in postMessage")
        # A transfer happens at the call, as Chrome does it: a port whose target
        # refuses the message is moved out of this realm all the same.
        #: The message is cloned before the list is applied, so a buffer the list is
        #: about to detach still carries its bytes; the sender's own context turns
        #: binary data into bytes rather than into an object with nothing enumerable on
        #: it, which is how an ArrayBuffer used to arrive as ``{}``.
        data = structured_clone(message, context=pyv8.current_context())
        ports = transfer_ports(transfer, services=self._services)
        serialized = serialize_origin(source._origin)

        def deliver():
            if self.get_closed() or (expected_origin is not None and expected_origin != self._origin):
                return
            self._services.realm_services.materialize_interface("MessageEvent")
            event = MessageEvent("message", {"data": data, "origin": serialized,
                                           "source": self._view(source), "ports": ports,
                                           "isTrusted": True})
            self.fn_dispatchEvent(event)
        self._services.realm_services.post_task(deliver)
