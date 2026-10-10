import threading
import time

import pytest
from loguru import logger

from pyv8c import Browser
from pyv8c.foundation.host_errors import HostStateError
from pyv8c.web.fetch.response import Response


def test_goto_downloads_html_and_waits_for_document_load():
    calls = []

    def loader(url, request):
        calls.append((url, request.get("mode")))
        if url.endswith("/app.js"):
            return Response("window.__downloaded = document.querySelector('#app').textContent", {"url": url})
        return Response(
            "<html><body><main id='app'>loaded</main><script src='/app.js'></script></body></html>",
            {"url": url, "headers": {"content-type": "text/html; charset=utf-8"}},
        )

    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    response = page.goto("http://example.test/index.html")

    assert response.url == "http://example.test/index.html"
    assert page.evaluate("[document.URL, document.readyState, __downloaded]") == [
        "http://example.test/index.html", "complete", "loaded",
    ]
    assert calls == [
        ("http://example.test/index.html", "navigate"),
        #: A classic script states no mode: it is a no-cors load of the page's own.
        ("http://example.test/app.js", None),
    ]
    browser.close()


def test_new_page_url_and_location_assignment_navigate_automatically():
    pages = {
        "http://example.test/first": "<html><body><h1>first</h1></body></html>",
        "http://example.test/next": "<html><body><h1>next</h1></body></html>",
    }
    browser = Browser(resource_loader=lambda url, _request: Response(pages[url], {"url": url}))
    page = browser.new_context().new_page("http://example.test/first")

    assert page.evaluate("[location.href, document.querySelector('h1').textContent]") == [
        "http://example.test/first", "first",
    ]
    page.evaluate("location.href = '/next'")
    page.run_until_idle(timeout=1)
    assert page.evaluate("[location.href, document.querySelector('h1').textContent]") == [
        "http://example.test/next", "next",
    ]
    page.url = "/first"
    assert page.evaluate("[location.href, document.querySelector('h1').textContent]") == [
        "http://example.test/first", "first",
    ]
    browser.close()


def test_document_location_is_the_windows_and_assigning_it_navigates():
    """The navigation an assignment performs needs a server; the corpus has none."""
    pages = {
        "http://example.test/first": "<html><body><h1>first</h1></body></html>",
        "http://example.test/next": "<html><body><h1>next</h1></body></html>",
    }
    browser = Browser(resource_loader=lambda url, _request: Response(pages[url], {"url": url}),
                      stub_logging="off")
    page = browser.new_context().new_page("http://example.test/first")
    try:
        assert page.evaluate("document.location === location") is True
        page.evaluate("document.location = '/next'")
        page.run_until_idle(timeout=1)
        assert page.evaluate("[location.href, document.location.href, "
                             "document.querySelector('h1').textContent]") == [
            "http://example.test/next", "http://example.test/next", "next",
        ]
    finally:
        browser.close()


def test_goto_decodes_http_charset_and_requires_a_loader_or_network_backend():
    html = "<html><body>中文页面</body></html>".encode("gb18030")
    browser = Browser(resource_loader=lambda url, _request: Response(
        html, {"url": url, "headers": {"content-type": "text/html; charset=gb18030"}},
    ))
    page = browser.new_context().new_page()
    page.goto("http://example.test/chinese")
    assert page.evaluate("document.body.textContent") == "中文页面"
    browser.close()

    browser = Browser(real_network=False)
    with pytest.raises(Exception, match="Network access requires"):
        browser.new_context().new_page("http://example.test/")
    browser.close()


def test_set_content_without_markup_starts_an_empty_standards_document():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        assert page.set_content() is page
        assert page.evaluate(
            "[document.compatMode, document.documentElement.tagName, document.body.textContent]"
        ) == ["CSS1Compat", "HTML", ""]
    finally:
        browser.close()


# -- a navigation that does not finish says why ---------------------------------


def test_a_navigation_that_times_out_names_the_state_and_what_is_still_loading():
    """A blocking script that never arrives is what the wait was about."""
    release = threading.Event()

    def loader(url, request):
        if url.endswith("/hangs.js"):
            release.wait(10)
        return Response(
            "<html><head><script src='/hangs.js'></script></head><body>waiting</body></html>",
            {"url": url, "headers": {"content-type": "text/html"}},
        )

    browser = Browser(stub_logging="off", threaded_loaders=True, resource_loader=loader)
    try:
        page = browser.new_context().new_page()
        started = time.perf_counter()
        with pytest.raises(HostStateError, match="waiting for domcontentloaded") as caught:
            page.goto("http://example.test/page", wait_until="domcontentloaded", timeout=0.5)
        elapsed = time.perf_counter() - started
        text = str(caught.value)
        assert "timed out after 0.5s" in text
        assert "still loading 1 script" in text
        assert "the document request took" in text
        # The navigation gives up, not the executor: the grace is there so the
        # reason survives instead of a RealmBusyError with nothing in it.
        assert elapsed < 0.5 + 5
    finally:
        release.set()
        browser.close()


def test_a_resource_that_fails_puts_its_reason_in_the_log():
    """The page hears Chrome's wording; the log keeps what actually happened."""
    messages = []
    token = logger.add(lambda message: messages.append(str(message).strip()), format="{message}")

    def loader(url, request):
        if url.endswith("/app.js"):
            raise RuntimeError("no such file")
        return Response(
            "<html><body><script src='/app.js'></script>loaded</body></html>",
            {"url": url, "headers": {"content-type": "text/html"}},
        )

    browser = Browser(stub_logging="off", resource_loader=loader)
    try:
        page = browser.new_context().new_page()
        page.goto("http://example.test/index.html", wait_until="load", timeout=5)
        page.run_until_idle(timeout=1)
    finally:
        logger.remove(token)
        browser.close()
    failed = [line for line in messages if "resource failed" in line]
    assert len(failed) == 1
    assert "http://example.test/app.js" in failed[0]
    assert "no such file" in failed[0]


def test_a_failed_script_tells_the_page_the_load_failed_and_nothing_more():
    """A load the host could not make is reported, not explained.

    The reason -- a path, a transport message, the host's own words -- is the host's,
    so neither the event's message nor the value a handler reads may carry it: a page
    that logs its errors, or reports them home, must not be able to read the host out
    of them.
    """
    reason = "loader blew up at D:/src/pyv8env/pyv8c/secret.py"

    def loader(url, request):
        if url.endswith("/app.js"):
            raise RuntimeError(reason)
        return Response(
            "<html><body><script>window.__errors = [];"
            "window.addEventListener('error', (event) =>"
            " window.__errors.push([event.message, String(event.error)]));</script>"
            "<script src='/app.js'></script>loaded</body></html>",
            {"url": url, "headers": {"content-type": "text/html"}},
        )

    browser = Browser(stub_logging="off", resource_loader=loader)
    try:
        page = browser.new_context().new_page()
        page.goto("http://example.test/index.html", wait_until="load", timeout=5)
        page.run_until_idle(timeout=1)
        errors = page.evaluate("window.__errors")
    finally:
        browser.close()

    assert errors == [["Failed to load the script", "undefined"]]
    assert "secret" not in str(errors)
