"""``Location``, ``History`` and the Navigation API: the realm's view of navigation.

Assigning ``location`` pushes or replaces a history entry, while ``history.back()``
assigns an entry's URL into ``location``.  A same-document (fragment) change is
committed without rebuilding the document, firing ``hashchange`` and no load events.
"""

from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8

from ...engine.features import LazyValueStore
from ...idl import interface
from ...foundation.arguments import MISSING, dictionary, dom_string, integer, is_nullish, missing_error, usv_string
from ..page_errors import SecurityError
from ..dom.collections import DOMStringList
from ..dom.events import EventTarget, HashChangeEvent, PopStateEvent, _install_handler_slots
from .utils import document_key

__all__ = ["Location", "History", "Navigation", "NavigationHistoryEntry"]


#: Location components backed by a real setter; protocol/host/hostname/port keep
#: the stub setter.
_LOCATION_SETTERS = {
    "href": "set_href",
    "pathname": "set_pathname",
    "search": "set_search",
    "hash": "set_hash",
}


@interface('Location', None)
class Location:
    def __init__(self, href="about:blank", services=None, **_): self.href, self._services = href, services
    def _require_access(self):
        if self._services is not None:
            target = self._services.document_services.window
            if target is not None:
                target._require_same_origin()
    def _parts(self): self._require_access(); return urlsplit(self.href)
    def get_href(self): self._require_access(); return self.href
    def set_href(self, value):
        value = dom_string(value)
        target = urljoin(self.href, str(value))
        if self._services is None:
            self.href = target
            return
        if self._is_same_document(target):
            self._navigate_within_document(target, replace=False)
            return
        self._services.document_services.request_navigation(target)
    def _is_same_document(self, target):
        return target != self.href and document_key(target) == document_key(self.href)
    def _navigate_within_document(self, target, replace):
        """Commit a fragment change without rebuilding the document: no
        ``beforeunload``/``pagehide``/``unload`` runs, but ``hashchange`` fires.
        """
        window = self._services.document_services.window
        # Materialize the History (which starts at the *current* URL) before the
        # fragment change so entry 0 still describes the document we are on.
        history = getattr(window, "history", None)
        previous, self.href = str(self.href), target
        if history is not None:
            if replace:
                history.replace_same_document(target)
            else:
                history.push_same_document(target)
        if urlsplit(previous).fragment != urlsplit(target).fragment:
            self._services.realm_services.materialize_interface("HashChangeEvent")
            event = HashChangeEvent("hashchange", {"oldURL": previous, "newURL": target})
            event._trusted = True
            window.fn_dispatchEvent(event)
    def _set_component(self, **changes):
        self._require_access()
        self.set_href(urlunsplit(urlsplit(self.href)._replace(**changes)))
    def set_pathname(self, value): self._set_component(path=dom_string(value))
    def set_search(self, value): self._set_component(query=dom_string(value).lstrip("?"))
    def set_hash(self, value): self._set_component(fragment=dom_string(value).lstrip("#"))
    def fn_assign(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Location', 'assign', 1, 0)
        self.set_href(usv_string(value))
    def fn_replace(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Location', 'replace', 1, 0)
        target = urljoin(self.href, usv_string(value))
        if self._services is None:
            self.href = target
            return
        if self._is_same_document(target):
            self._navigate_within_document(target, replace=True)
            return
        self._services.document_services.request_navigation(target)
    def fn_reload(self, *_extra):
        if self._services is not None:
            self._services.document_services.request_navigation(self.href)
    def get_origin(self):
        p = self._parts(); return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else "null"
    def get_protocol(self): return self._parts().scheme + ":"
    def get_host(self): return self._parts().netloc
    def get_hostname(self): return self._parts().hostname or ""
    def get_port(self): return str(self._parts().port or "")
    def get_pathname(self): return self._parts().path or "/"
    def get_search(self): return ("?" + self._parts().query) if self._parts().query else ""
    def get_hash(self): return ("#" + self._parts().fragment) if self._parts().fragment else ""
    def get_ancestorOrigins(self):
        """The origins above this document, nearest first, as one list for good."""
        origins = getattr(self, "_ancestorOrigins", None)
        if origins is None:
            realm = getattr(self._services, "realm_services", None)
            if realm is not None:
                realm.materialize_interface("DOMStringList")
            origins = self._ancestorOrigins = DOMStringList(
                () if realm is None else realm.ancestor_origins())
        return origins
    def fn_toString(self, *_extra): return self.get_href()


@interface('History', None)
class History:
    def __init__(self, services=None, initial_url=None, **_):
        self._services = services
        self._entries = [(initial_url if initial_url is not None else getattr(getattr(services, "realm_services", None), "url", "about:blank"), None)]
        self._index = 0
    def get_length(self): return len(self._entries)
    def get_state(self):
        state = self._entries[self._index][1]
        return pyv8.Null if state is None else state
    def _url(self, value): return urljoin(self._services.realm_services.url, str(value))
    def _validate_target(self, url):
        from ...foundation.urls import same_origin
        target = self._url(url) if url is not None else self._services.realm_services.url
        if not same_origin(self._services.realm_services.url, target): raise SecurityError("A history state URL must be same-origin")
        return target
    def fn_pushState(self, state=MISSING, unused=MISSING, url=None, *_extra):
        if state is MISSING:
            raise missing_error('History', 'pushState', 2, 0, constructor=False)
        if unused is MISSING:
            raise missing_error('History', 'pushState', 2, 1, constructor=False)
        unused = dom_string(unused)
        if url is pyv8.Undefined:
            url = None
        url = None if is_nullish(url) else usv_string(url)
        target = self._validate_target(url)
        self._entries = self._entries[:self._index + 1] + [(target, state)]; self._index += 1; self._services.document_services.window.location.href = target
    def fn_replaceState(self, state=MISSING, unused=MISSING, url=None, *_extra):
        if state is MISSING:
            raise missing_error('History', 'replaceState', 2, 0, constructor=False)
        if unused is MISSING:
            raise missing_error('History', 'replaceState', 2, 1, constructor=False)
        unused = dom_string(unused)
        if url is pyv8.Undefined:
            url = None
        url = None if is_nullish(url) else usv_string(url)
        target = self._validate_target(url)
        self._entries[self._index] = (target, state); self._services.document_services.window.location.href = target
    def push_same_document(self, url, state=None):
        """Record a same-document entry without resolving the URL again."""
        self._entries = self._entries[:self._index + 1] + [(str(url), state)]
        self._index += 1
    def replace_same_document(self, url, state=None):
        self._entries[self._index] = (str(url), state)
    def fn_back(self, *_extra):
        return self.fn_go(-1)
    def fn_forward(self, *_extra):
        return self.fn_go(1)
    def fn_go(self, delta=0, *_extra):
        if delta is pyv8.Undefined:
            delta = 0
        if delta is not pyv8.Undefined:
            delta = integer(delta, signed=True)
        target = max(0, min(len(self._entries) - 1, self._index + int(delta)))
        if target == self._index: return
        window = self._services.document_services.window
        previous = str(window.location.href)
        self._index = target
        entry = self._entries[target]
        window.location.href = entry[0]
        self._services.realm_services.materialize_interface("PopStateEvent")
        event = PopStateEvent("popstate", {"state": entry[1]})
        event._trusted = True
        window.fn_dispatchEvent(event)
        # Crossing entries whose fragments differ is also a fragment navigation,
        # so ``hashchange`` follows ``popstate`` for that step.
        if urlsplit(previous).fragment != urlsplit(str(entry[0])).fragment:
            self._services.realm_services.materialize_interface("HashChangeEvent")
            change = HashChangeEvent("hashchange", {"oldURL": previous, "newURL": str(entry[0])})
            change._trusted = True
            window.fn_dispatchEvent(change)


#: The Navigation API's handler slots.
HANDLERS = ("oncurrententrychange", "onnavigate", "onnavigateerror", "onnavigatesuccess")


def _navigation_values(navigation):
    """The sub-objects a Navigation hands out, built on first read."""
    return LazyValueStore({
        "currentEntry": lambda: NavigationHistoryEntry(
            navigation._window.location.get_href(), 0,
            None if not hasattr(navigation, "_state") else navigation._state),
        "activation": lambda: NavigationActivation(navigation._current()),
    })


@interface('NavigationHistoryEntry', 'EventTarget')
class NavigationHistoryEntry(EventTarget):
    def __init__(self, url="", index=0, state=None, **_):
        super().__init__(**_)
        self._url, self._index, self._state = str(url), int(index), state
        self._key = str(index)

    def get_url(self): return self._url
    def get_key(self): return self._key
    def get_id(self): return self._key
    def get_index(self): return self._index
    def get_sameDocument(self): return True
    def get_ondispose(self): return self._get_handler("ondispose")
    def set_ondispose(self, value): self._set_handler("ondispose", value)

    def fn_getState(self, *_extra):
        return pyv8.Undefined if self._state is None else self._state

    def set_state(self, state):
        self._state = state


@interface('NavigationActivation', None)
class NavigationActivation:
    """What the last activation says, from the entry the page is on."""

    def __init__(self, entry=None, **_):
        self._entry = entry

    def get_entry(self):
        return pyv8.Null if self._entry is None else self._entry

    def get_from(self): return pyv8.Null
    def get_navigationType(self): return "push"


@interface('Navigation', 'EventTarget')
class Navigation(EventTarget):
    def __init__(self, window=None, **_):
        super().__init__(**_)
        self._window = window
        self._values = _navigation_values(self)

    def _current(self): return self._values.value("currentEntry")

    def get_currentEntry(self): return self._current()
    def fn_entries(self, *_extra): return [self._current()]
    def get_canGoBack(self): return False
    def get_canGoForward(self): return False
    def get_transition(self): return pyv8.Null

    def get_activation(self): return self._values.value("activation")

    def fn_navigate(self, url=MISSING, options=None, *_extra):
        """Ask for the navigation, and answer with the two promises it reports
        through; they carry this realm's entry, not the new document's.
        """
        if url is MISSING:
            raise missing_error('Navigation', 'navigate', 1, 0, constructor=False)
        target = urljoin(self._window.location.get_href(), dom_string(url))
        return self._navigate_to(target)

    def fn_reload(self, options=None, *_extra):
        return self._navigate_to(self._window.location.get_href())

    def _navigate_to(self, target):
        services = self._window._services
        entry = self._current()
        committed, resolve_committed, _reject = services.realm_services.make_promise()
        finished, resolve_finished, _reject_again = services.realm_services.make_promise()
        services.document_services.request_navigation(target)
        resolve_committed(entry)
        resolve_finished(entry)
        return {"committed": committed, "finished": finished}

    #: There is no history to walk in either direction.
    def fn_back(self, *_extra): return None
    def fn_forward(self, *_extra): return None
    def fn_traverseTo(self, key=MISSING, *_extra): return None

    def fn_updateCurrentEntry(self, options=MISSING, *_extra):
        if options is MISSING:
            raise missing_error('Navigation', 'updateCurrentEntry', 1, 0, constructor=False)
        settings = dictionary(options)
        self._state = settings.get("state")
        self._current().set_state(self._state)


#: Accessors only: the four handlers the baseline publishes are bound by the convention.
_install_handler_slots(Navigation, HANDLERS)
