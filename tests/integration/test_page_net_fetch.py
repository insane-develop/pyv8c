"""``page.net.fetch``/``page.net.xhr``: host calls that run the page's own JS APIs.

What these methods are for is the implementation the *page* has: a page that
replaced ``fetch`` or ``XMLHttpRequest`` -- instrumentation, a test harness -- is
the one that answers, and the request carries the page's rules (CORS, the
interceptor, the cache) instead of the transport the ``get``/``post`` helpers run on.
"""
import json

import pytest

from pyv8c import Browser

#: ``NetworkError`` and ``InvalidStateError`` are DOMException factories here, so
#: the raised type is the exception they build.
from pyv8c.foundation.host_errors import LoadFailure

DOCUMENT = "<body></body>"


def loader(url, request):
    """Answer with what the request asked for, so a test can read it back."""
    return {
        "url": url,
        "headers": {"content-type": "application/json", "x-demo": "1"},
        "body": json.dumps({
            "method": request.get("method"),
            "url": url,
            "body": request.get("body"),
        }),
    }


@pytest.fixture
def page():
    browser = Browser(real_network=False, resource_loader=loader, stub_logging="off")
    try:
        yield browser.new_context().new_page("http://example.test/")
    finally:
        browser.close()


def test_fetch_reads_the_response_through_the_page_stack(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    answer = page.net.fetch("/echo")

    assert (answer.ok, answer.status, answer.url) == (True, 200, "http://example.test/echo")
    assert answer.headers["x-demo"] == "1"
    assert answer.json()["url"] == "http://example.test/echo"
    assert answer.text == answer.body


def test_fetch_posts_a_body_and_headers(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    answer = page.net.fetch("/echo", method="POST", body="payload", headers={"X-Test": "1"})

    seen = answer.json()
    assert seen["method"] == "POST"
    assert seen["body"] == "payload"


def test_fetch_uses_a_replaced_fetch(page):
    """The page's own ``fetch`` is the implementation that runs."""
    page.set_content(DOCUMENT, url="http://example.test/")
    page.evaluate("""
      globalThis.seen = [];
      globalThis.fetch = (input, init) => {
        seen.push([String(input), init && init.method]);
        return Promise.resolve({
          ok: true, status: 299, statusText: 'Replaced', url: 'http://example.test/ovr',
          redirected: true, headers: {entries: () => [['x-made', 'by-page']]},
          text: () => Promise.resolve('from the page'),
        });
      };
    """)

    answer = page.net.fetch("/hooked", method="PUT")

    assert page.evaluate("seen") == [["/hooked", "PUT"]]
    assert (answer.status, answer.status_text, answer.url) == (299, "Replaced", "http://example.test/ovr")
    assert (answer.headers, answer.body, answer.redirected) == ({"x-made": "by-page"}, "from the page", True)


def test_fetch_async_settles_on_the_realm_queue(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    future = page.net.fetch_async("/echo")

    answer = future.result(timeout=10)
    assert answer.json()["url"] == "http://example.test/echo"


def test_a_rejected_fetch_raises_the_pages_error(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    page.evaluate("globalThis.fetch = () => Promise.reject(new TypeError('nope'));")

    with pytest.raises(LoadFailure, match="nope"):
        page.net.fetch("/echo")
    with pytest.raises(LoadFailure, match="TypeError"):
        page.net.fetch_async("/echo").result(timeout=10)


def test_a_fetch_that_never_settles_times_out(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    page.evaluate("globalThis.fetch = () => new Promise(() => {});")

    with pytest.raises(TimeoutError, match="did not settle"):
        page.net.fetch("/forever", timeout=0.2)


def test_xhr_reads_the_response_text(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    answer = page.net.xhr("/echo")

    assert (answer.ok, answer.status) == (True, 200)
    assert answer.json()["url"] == "http://example.test/echo"


def test_xhr_uses_a_replaced_xmlhttprequest(page):
    """A page that replaced the constructor is the one the host reaches."""
    page.set_content(DOCUMENT, url="http://example.test/")
    page.evaluate("""
      globalThis.made = 0;
      const Original = globalThis.XMLHttpRequest;
      globalThis.XMLHttpRequest = function () { made += 1; return new Original(); };
    """)

    page.net.xhr("/echo")

    assert page.evaluate("made") == 1


def test_xhr_async_settles_on_the_realm_queue(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    future = page.net.xhr_async("/echo")

    assert future.result(timeout=10).json()["url"] == "http://example.test/echo"


def test_a_closed_page_refuses_both(page):
    page.set_content(DOCUMENT, url="http://example.test/")
    page.close()

    with pytest.raises(RuntimeError, match="closed"):
        page.net.fetch("/echo")
    with pytest.raises(RuntimeError, match="closed"):
        page.net.xhr("/echo")
    # The async form hands the closed realm back through its future.
    with pytest.raises(RuntimeError, match="closed"):
        page.net.fetch_async("/echo").result(timeout=1)
