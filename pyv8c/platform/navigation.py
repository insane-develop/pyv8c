"""Navigation and HTML document parsing for a single Page."""

import re
import time

from ..foundation.host_errors import HostStateError
from ..foundation.tasks import TaskSource
from ..foundation.urls import absolute_url
from ..network.redirects import reportable_hops
from ..web.bom.utils import page_base_url, page_secure_context
from ..web.dom.document import Document, HTMLDocument
from ..web.dom.motion import INSECURE_ORIGIN_INTERFACES
from ..web.dom.parser import IncrementalDOMParser


#: How much longer than the navigation's own budget the executor waits: the
#: navigation knows what it was waiting for, so it must be the one that gives up.
NAVIGATION_GRACE = 5.0


def hide_insecure_origin_interfaces(page):
    """Take the motion interfaces out of a realm whose document is not secure."""
    context = getattr(page, "context", None)
    if context is None:
        return
    for name in INSECURE_ORIGIN_INTERFACES:
        context.hide_global(name)


class NavigationController:
    """Own navigation and parser state; runs on the owning Page thread."""

    def __init__(self, page):
        self.page = page
        self.generation = 0
        self.has_navigation = False
        self.pending = 0
        self.parser = None
        self.parser_generation = 0
        self.parsing = False
        self.parser_done = False

    def navigation_allowed(self):
        return not self.has_navigation or self.page.lifecycle.dispatch_beforeunload()

    def commit_content(self, html, url, response=None):
        lifecycle = self.page.lifecycle
        if self.has_navigation:
            # Chrome's order for the document being replaced: pageswap, pagehide, the
            # document going hidden, then unload.
            lifecycle.dispatch_pageswap()
            lifecycle.dispatch_page_hide(False)
            lifecycle.dispatch_visibility_hidden()
            lifecycle.dispatch_unload()
        self.generation += 1
        # A later document replaces the realm; the first one is the page's own.
        renew_realm = self.has_navigation
        self.has_navigation = True
        self.parser_done = lifecycle.dom_content_loaded = lifecycle.document_load_fired = False
        self.page.resources.reset()
        self.page.scripts.reset()
        if renew_realm:
            # The replaced document's timers go with it; the queue belongs to the page
            # and outlives the document.
            self.page.tasks.cancel_timers()
            self.page._renew_document_realm(str(url))
        self.page._update_origin(url)
        if not page_secure_context(self.page, url):
            # Chrome exposes these only on a secure origin; a frame commits while its
            # parent's realm runs, so the names leave on a task of their own.
            page = self.page
            page.tasks.post(lambda: hide_insecure_origin_interfaces(page))
        self.page.window.location.href = str(url)
        self.page.window.sync_history_url(str(url))
        devtools = self.page.context_owner._devtools
        if devtools is not None:
            devtools.target_info_changed(self.page)
        self.page.document = HTMLDocument(services=self.page.services, url=str(url),
                                          ready_state="loading")
        # A new document starts its own timeline; earlier records belong to the old one.
        hops = int(getattr(response, "_redirect_hops", 0) or 0)
        crossed = bool(getattr(response, "_redirect_crossed", False))
        counted = reportable_hops(hops, crossed)
        self.page.services.realm_services.performance.begin_navigation(
            url, redirects=counted,
            # A chain that reports nothing reports no window either, as Chrome does.
            redirects_span=float(getattr(response, "_redirect_span", 0.0) or 0.0) if counted else 0.0)
        self.page.services.document = self.page.document
        target = self.page._devtools_target
        if target is not None:
            target.wait_for_startup()
        self.parser_generation = self.generation
        self.parser = IncrementalDOMParser(self.page.services, self.page.document, str(html))
        self.parsing = True
        # HTML parsing runs on the caller thread until a parser-blocking external
        # script is hit; lifecycle notifications stay queued to the task boundary.
        self.resume_parser(self.parser_generation)
        return self.page

    def set_content(self, html, url="about:blank"):
        if self.page.closed or self.page.context_owner.closed:
            raise HostStateError("Page is closed")
        if not self.navigation_allowed():
            return self.page
        return self.commit_content(html, url)

    @staticmethod
    def decode_html(response):
        body = response.body
        if not isinstance(body, bytes):
            return str(body)
        content_type = response.headers.fn_get("content-type")
        match = re.search(r"charset\s*=\s*['\"]?([^\s;'\"]+)", str(content_type), re.I)
        encoding = match.group(1) if match else "utf-8"
        try:
            return body.decode(encoding, "replace")
        except LookupError:
            return body.decode("utf-8", "replace")

    def goto(self, url, *, timeout=30, wait_until="load"):
        """Download and navigate to an HTML document."""
        return self._load(url, timeout=timeout, wait_until=wait_until, browser_initiated=True)

    def _load(self, url, *, timeout, wait_until, browser_initiated,
              method="GET", body=None, content_type=None, user_activated=False):
        """Load *url*, carrying whatever the request that asked for it must send.

        Only a browser-initiated navigation sends no ``Referer``; a user gesture
        is the other source of ``Sec-Fetch-User``.
        """
        if self.page.closed or self.page.context_owner.closed:
            raise HostStateError("Page is closed")
        states = {
            None: None,
            "commit": None,
            "domcontentloaded": lambda: self.page.lifecycle.dom_content_loaded,
            "load": lambda: self.page.lifecycle.document_load_fired,
        }
        if wait_until not in states:
            raise ValueError("wait_until must be 'commit', 'domcontentloaded', or 'load'")
        target = absolute_url(url, page_base_url(self.page))
        if not self.navigation_allowed():
            return None
        timeout_value = None if timeout is None else max(0.0, float(timeout))
        request = {
            "method": method, "mode": "navigate", "credentials": "include",
            "resource_type": "document", "destination": "document",
            "browser_initiated": browser_initiated,
            "user_activated": user_activated,
            "timeout": timeout_value,
        }
        if body is not None:
            request["body"] = body
        if content_type:
            request["content_type"] = content_type
        started = time.perf_counter()
        response = self.page.resources.load_resource(target, request)
        document_seconds = time.perf_counter() - started
        final_url = response.url or target
        self.commit_content(self.decode_html(response), final_url, response)
        ready = states[wait_until]
        if ready is not None:
            # Wait on the lifecycle state, not a fixed-interval poll: the pump
            # sleeps on the realm executor and the advancing delivery wakes it.
            # The document and this wait share one budget, so ``timeout`` bounds
            # the navigation rather than each half of it.
            limit = 30.0 if timeout_value is None else timeout_value
            remaining = limit - document_seconds
            if remaining > 0 and self.page.tasks.pump_until(ready, timeout=remaining):
                return response
            outstanding = self.page.resources.outstanding()
            waiting = ", ".join(f"{count} {kind}" for kind, count in sorted(outstanding.items()))
            raise HostStateError(
                f"Navigation to {target} timed out after {limit:g}s waiting for {wait_until}"
                + (f"; still loading {waiting}" if waiting else "")
                + f" (the document request took {document_seconds:.1f}s)"
            )
        return response

    def request_navigation(self, url, *, method="GET", body=None,
                           content_type=None, user_activated=False):
        """Queue a navigation a script, a link or a form submission asked for."""
        if self.page.closed or self.page.context_owner.closed:
            raise HostStateError("Page is closed")
        target = absolute_url(url, page_base_url(self.page))
        self.pending += 1
        request_id = self.pending

        def navigate():
            if request_id == self.pending and not self.page.closed:
                self._load(target, timeout=30, wait_until="load",
                           browser_initiated=False, method=method, body=body,
                           content_type=content_type,
                           user_activated=user_activated)

        self.page.tasks.post(navigate)
        return target

    def parser_is_blocked(self):
        return bool(self.page.scripts.dom_blocking_scripts)

    def write_markup(self, markup):
        """Insert JavaScript-written markup (HTML's ``document.write``).

        During parsing the markup joins the token stream at the writer's insertion
        point; afterwards ``document.write`` implies ``document.open()`` -- the
        document is emptied and parsed inside a fresh ``body``, keeping its URL,
        load state and event history.
        """
        parser = self.parser
        if parser is not None and not parser.done:
            return parser.write_markup(markup)
        if not self.open_document():
            return False
        body = self._open_document_body(self.page.document)
        self.parser = IncrementalDOMParser(self.page.services, body, str(markup))
        self.resume_parser(self.parser_generation)
        return True

    def open_document(self):
        """The document open steps: an empty document, reading ``loading`` as Chrome's does."""
        if self.page.closed or self.page.context_owner.closed:
            return False
        document = self.page.document
        self.generation += 1
        self.parser_generation = self.generation
        self.parser_done = False
        document.ready_state = "loading"
        self.page.resources.reset()
        self.page.scripts.reset()
        for child in list(document.get_childNodes() or ()):
            document.fn_removeChild(child)
        self.parser = None
        return True

    def _open_document_body(self, document):
        """Build the skeleton ``document.open()`` leaves for the new parse.

        A browser restarts the insertion modes, so written markup lands inside a
        fresh ``html``/``body``, not beside the document element.
        """
        element_class = self.element_class
        html = element_class("html")("html", services=self.page.services, attributes={})
        document.append_child(html)
        html.append_child(element_class("head")("head", services=self.page.services, attributes={}))
        body = element_class("body")("body", services=self.page.services, attributes={})
        html.append_child(body)
        return body

    def finish_document_parse(self):
        self.parser_done = True
        self.parsing = False
        self.page.tasks.post(self.page.scripts.run_deferred_scripts, TaskSource.PARSER)

    def close_document(self):
        """The document close steps: stop the parser where it stands.

        A page that opened a document and wrote part of it reads ``complete`` at once,
        and the markup it never wrote is not parsed.
        """
        if self.page.closed or self.page.context_owner.closed:
            return False
        self.parser = None
        if not self.parser_done:
            self.finish_document_parse()
        self.page.document.ready_state = "complete"
        return True

    def resume_parser(self, generation=None):
        if self.page.closed or generation != self.generation or self.parser is None:
            return
        if self.parser_is_blocked():
            return
        self.parsing = True
        try:
            complete = self.parser.run()
        except BaseException as exc:  # a script error is dispatched, not raised
            self.parsing = False
            self.page.lifecycle.dispatch_script_error(None, exc)
            complete = True
        if complete and self.parser is not None and self.parser.done:
            self.finish_document_parse()
        else:
            self.parsing = False

    def parser_script_start(self, node):
        node._parser_script = True
        node._resource_started = False
        node._resource_done = False
        node._resource_error = None
        node._resource_response = None
        # A script fetch starts when its start tag is seen, so async/defer/module
        # resources progress while the parser still collects their raw text.
        if getattr(node, "get_src", lambda: "")():
            self.page.resources.discover_element_resource(node)

    def parser_script_complete(self, node, source):
        scripts = self.page.scripts
        node._raw_text = source
        script_type = str(getattr(node, "get_type", lambda: "")() or "").lower()
        is_module = script_type == "module"
        is_async = bool(getattr(node, "get_async", lambda: False)())
        # async wins over defer: an asynchronous script is not in the list the document
        # waits for before DOMContentLoaded, and queueing it there wedges the lifecycle.
        is_defer = not is_async and (
            bool(getattr(node, "get_defer", lambda: False)()) or is_module)
        node._resource_async = is_async
        node._script_defer = is_defer
        if node.get_src():
            if is_defer:
                scripts.defer.append(node)
            elif not is_async:
                if not getattr(node, "_resource_queued", False):
                    scripts.dom_blocking_scripts += 1
                    scripts.queue.append(node)
                    node._resource_queued = True
            self.page.resources.discover_element_resource(node)
        elif source:
            node._inline_source = source
            if is_defer:
                node._resource_done = True
                scripts.defer.append(node)
            elif is_async:
                self.page.tasks.post(lambda n=node: scripts.execute_script(n, n._inline_source, self.page.url))
            else:
                scripts.execute_script(node, source, self.page.url)

    def element_class(self, tag):
        from ..web.dom.html_classes import class_for_tag
        return class_for_tag(tag)
