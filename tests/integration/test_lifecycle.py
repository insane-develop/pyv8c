from pyv8c import Browser
from pyv8c.web.fetch.response import Response


def test_document_lifecycle_and_resource_events_are_queued():
    paused = []

    def loader(url, request):
        if url.endswith("/ext.js"):
            # Sampled while the blocking script is outstanding: it has not
            # executed yet and the document is still in its loading phase.
            paused.append(page.evaluate("[document.readyState, typeof window.__external]"))
            return Response("window.__order.push('external'); window.__external = true", {"url": url, "content-type": "text/javascript"})
        return Response("", {"url": url})

    browser = Browser(resource_loader=loader)
    page = browser.new_context().new_page()
    page.evaluate("""
        window.__events = []; window.__order = [];
        window.addEventListener('DOMContentLoaded', e => __events.push(['dom', e.target === document, e.currentTarget === window]));
        // A window's load event targets the document, as Chrome's does.
        window.addEventListener('load', e => __events.push(['window-load', e.target === document, e.currentTarget === window]));
        window.addEventListener('pageshow', () => __events.push('pageshow'));
    """)
    page.set_content("""
        <html><head>
          <script>document.addEventListener('readystatechange', () => __events.push(['rs', document.readyState])); __events.push('inline'); window.__order.push('first');</script>
          <script src='/ext.js'></script>
          <script>window.__order.push('after-external');</script>
        </head><body><img src='/image.png'><script>document.querySelector('img').addEventListener('load', () => __events.push('image-load'));</script></body></html>
    """, url="http://example.test/")

    assert paused == [["loading", "undefined"]]
    page.run_until_idle()
    events = page.evaluate("__events")
    assert page.evaluate("[document.readyState, window.__external, window.__order]") == ["complete", True, ["first", "external", "after-external"]]
    assert "inline" in events and "image-load" in events
    assert ["dom", True, True] in events
    assert ["rs", "interactive"] in events and ["rs", "complete"] in events
    assert events.index(["dom", True, True]) < events.index(["window-load", True, True]) < events.index("pageshow")
    browser.close()


def test_navigation_teardown_and_beforeunload_cancellation():
    browser = Browser()
    page = browser.new_context().new_page()
    page.set_content("""<body></body><script>
        const mark = value => sessionStorage.setItem('teardown',
            ((sessionStorage.getItem('teardown') || '') + ' ' + value).trim());
        addEventListener('beforeunload', () => mark('beforeunload'));
        addEventListener('pageswap', () => mark('pageswap'));
        addEventListener('pagehide', e => mark('pagehide:' + e.persisted));
        addEventListener('visibilitychange',
            () => mark('visibilitychange:' + document.visibilityState));
        addEventListener('unload', () => mark('unload'));
    </script>""", url="http://example.test/old")
    page.run_until_idle()
    page.set_content("<body>new</body>", url="http://example.test/new")
    # Chrome's order for the document being replaced: pageswap, pagehide, the document
    # going hidden, then unload.
    assert page.evaluate("sessionStorage.getItem('teardown')") == \
        "beforeunload pageswap pagehide:false visibilitychange:hidden unload"
    assert page.evaluate("document.URL") == "http://example.test/new"

    page.evaluate("window.onbeforeunload = e => e.preventDefault()")
    page.set_content("<body>blocked</body>", url="http://example.test/blocked")
    assert page.evaluate("[document.URL, document.body.textContent]") == ["http://example.test/new", "new"]
    browser.close()
