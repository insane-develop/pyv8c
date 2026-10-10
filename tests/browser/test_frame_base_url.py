"""A framed document with no address of its own still resolves relative loads.

``about:blank`` and ``about:srcdoc`` carry no scheme, so a browser takes the base URL from the
document that created them.  Resolution used to read the frame's own URL instead, which turned
``<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js">`` into a bare path -- refused
as "not a scheme a page loads", so the script was never requested at all (measured on the live
Cloudflare challenge, 2026-10-10, see ``examples/5s/README.md``).

Two-sided reading of the same three shapes: ``examples/5s/script/base_url_frames.py``.  Chrome
resolves the plain ``srcdoc`` and the ``about:blank`` cases; the sandboxed ``srcdoc`` one it
*does* drop (an open divergence, recorded there, not asserted here as a Chrome fact).
"""
import json
import threading
import time

from pyv8c import Browser

URL = "http://frames.test/page.html"

#: Every URL the loader was asked for, in order.
SEEN = []


def _page(frame_attributes, script_src):
    """The page, with one frame whose markup asks for *script_src*."""
    markup = f'<script src="{script_src}"></scr' + "ipt>"
    return (
        "<!doctype html><html><body><script>"
        "var frame = document.createElement('iframe');"
        f"{frame_attributes}"
        "frame.setAttribute('srcdoc', __MARKUP__);"
        "document.body.appendChild(frame);"
        "</" + "script></body></html>"
    ).replace("__MARKUP__", json.dumps(markup).replace("</", "<\\/"))


def _load(page_html):
    def loader(url, request):
        SEEN.append(str(url))
        if str(request.get("destination")) == "document" or str(url).endswith(".html"):
            return {"body": page_html, "headers": {"content-type": "text/html"}}
        return {"body": "/* a script */", "headers": {"content-type": "text/javascript"}}
    return loader


def _pump(page, seconds=1.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001 - a busy realm is retried, not asserted on
            pass
        time.sleep(0.05)


def _bounded(action, seconds=10.0):
    thread = threading.Thread(target=action, daemon=True)
    thread.start()
    thread.join(seconds)


def _asked_for(page_html):
    """Serve *page_html* to one page and report the URLs its loader was asked for."""
    del SEEN[:]
    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=_load(page_html))
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        _pump(page)
    finally:
        _bounded(browser.close)
    return list(SEEN)


def test_a_srcdoc_frame_resolves_a_root_relative_script_against_its_creator():
    seen = _asked_for(_page("", "/frame-relative.js"))
    assert "http://frames.test/frame-relative.js" in seen, seen
    assert "/frame-relative.js" not in seen, seen


def test_a_sandboxed_srcdoc_frame_resolves_it_the_same_way():
    """The widget frame's shape: ``sandbox`` takes the origin away, never the base URL."""
    seen = _asked_for(_page("frame.setAttribute('sandbox', 'allow-scripts');",
                            "/sandboxed-relative.js"))
    assert "http://frames.test/sandboxed-relative.js" in seen, seen
    assert "/sandboxed-relative.js" not in seen, seen


def test_a_document_with_its_own_url_still_resolves_against_itself():
    """The creator walk must not reach past a document that has an address of its own."""
    seen = _asked_for(
        "<!doctype html><html><body>"
        '<script src="/page-relative.js"></scr' + "ipt>"
        "</body></html>"
    )
    assert "http://frames.test/page-relative.js" in seen, seen
