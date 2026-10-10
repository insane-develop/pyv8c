"""A page cannot make the network stack load a scheme the browser owns.

Chrome fails ``chrome-extension://`` locally and never puts it on the wire; the values
below are the ones Chrome 154.0.8037.98 shows a page.
"""
import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c import Browser  # noqa: E402

EXTENSION_URL = "chrome-extension://kjacjjdnoddnpbbcjilcajfhhbdhkpg/images/about/en/1.png"


def test_a_browser_own_scheme_never_reaches_the_engine():
    """The failure is the browser's, and the network engine is never asked."""
    asked = []

    class Transport:
        """The engine ``http_engine`` builds: reaching it at all fails the test."""

        def request_sync(self, request):
            asked.append(request.url)
            raise AssertionError("the network engine was asked for a browser scheme")

        def request_async(self, tasks, request, callback):
            asked.append(request.url)
            raise AssertionError("the network engine was asked for a browser scheme")

    browser = Browser(stub_logging="off", http_engine=Transport)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="http://example.test/")
        page.evaluate("""
          fetch(%s).then(
            () => { window.__fetched = ['resolved']; },
            (error) => { window.__fetched = [error.name, error.message,
                                            error instanceof TypeError]; });
          const xhr = new XMLHttpRequest();
          xhr.onerror = (event) => { window.__xhr = [xhr.status, xhr.statusText,
              xhr.responseText, xhr.response, xhr.responseType, String(xhr.responseXML),
              xhr.readyState, event.type, event.message === undefined]; };
          xhr.open('GET', %s);
          xhr.send();
        """ % (repr(EXTENSION_URL), repr(EXTENSION_URL)))
        page.run_until_idle(1)

        assert page.evaluate("window.__fetched") == ["TypeError", "Failed to fetch", True]
        assert page.evaluate("window.__xhr") == [0, "", "", "", "", "null", 4, "error", True]

        # The same URL as an image fails the way an element reports a load failure.
        page.evaluate("window.__image = new Image(); window.__image.src = %s;"
                      % repr(EXTENSION_URL))
        page.run_until_idle(1)
        assert page.evaluate("[window.__image.complete, window.__image.naturalWidth]") == [True, 0]
    finally:
        browser.close()
    assert asked == []
