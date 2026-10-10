"""A preload link is fetched, and an image answers for the size its file declares.

Both halves were missing: nothing fetched ``<link rel=preload>``, so a page could tell that a
resource it asked for never arrived, and no image reported a size at all, so a challenge that
measures one had nothing to read.  The size comes from the file's header -- no decoder here.
"""
import json
import threading
import time

from pyv8c import Browser

LOGO = ("<svg xmlns='http://www.w3.org/2000/svg' width='88' height='33'>"
        "<rect width='88' height='33' fill='#333'/></svg>")

PAGE_HTML = """<!doctype html><head>
<link rel="preload" as="image" href="/logo.svg">
<link rel="preload" as="font" href="/quiet.woff2" crossorigin>
</head><body>
<img id="logo" src="/logo.svg">
<script>
var out = {}; window.__out = out;
var image = document.getElementById('logo');
image.addEventListener('load', function () { out.loaded = true; });
out.seen = [];
</script></body>"""

PROBE_URL = "https://preload.test/"
FETCHED = []


def _bounded(action, seconds=10.0):
    done = threading.Event()

    def run():
        try:
            action()
        except BaseException:  # noqa: BLE001
            pass
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    done.wait(seconds)


def _pump(page, seconds=0.6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        time.sleep(0.01)


def _load(url, request):
    target = str(url)
    FETCHED.append(target)
    if target.endswith("logo.svg"):
        return {"body": LOGO, "headers": {"content-type": "image/svg+xml"}}
    if target.endswith("quiet.woff2"):
        return {"body": b"\x00\x01\x00\x00", "headers": {"content-type": "font/woff2"}}
    return {"body": PAGE_HTML, "headers": {"content-type": "text/html"}}


def main(seconds=0.6):
    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=_load)
    try:
        page = browser.new_context().new_page()
        page.goto(PROBE_URL)
        _pump(page, seconds)
        return page, json.loads(page.evaluate("""JSON.stringify((() => {
            const image = document.getElementById('logo');
            return {width: image.naturalWidth, height: image.naturalHeight,
                    complete: image.complete,
                    font: performance.getEntriesByType('resource')
                      .map(e => String(e.name)).filter(n => n.indexOf('woff2') >= 0)};
        })())"""))
    finally:
        _bounded(browser.close)


def test_an_image_reports_the_size_its_file_declares():
    _page, reading = main()
    assert reading["width"] == 88, reading
    assert reading["height"] == 33, reading
    assert reading["complete"] is True, reading


def test_a_preload_link_is_fetched_even_though_nothing_uses_it():
    del FETCHED[:]
    _page, reading = main()
    assert [target for target in FETCHED if target.endswith("quiet.woff2")], FETCHED
    assert reading["font"], reading
