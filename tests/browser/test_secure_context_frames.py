"""A frame whose address has no scheme of its own: which context does it sit in?

``about:blank`` and ``about:srcdoc`` documents inherit their security context from the page
that created them, so an inline frame inside an ``https`` page answers
``isSecureContext === true`` and exposes the secure-context-only ``navigator`` members.  Judging
such a frame by its own URL instead answered ``false`` with thirty of those members
``undefined`` -- which is the probe Cloudflare's widget makes (measured against Chrome
2026-10-06 with ``examples/5s/script/secure_frame.py``).  A frame of an insecure page has
nothing to inherit, so it must stay insecure: the chain is a fallback, not a promotion.
"""
import json
import threading
import time

import pytest

from pyv8c import Browser

#: The members Cloudflare's widget reads, all of them secure-context only.
MEMBERS = ["userActivation", "clipboard", "credentials", "mediaDevices", "storage"]


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
        except BaseException:  # noqa: BLE001 - a JS error on the way is not this test's
            pass
        time.sleep(0.01)


#: The frame reports itself, because a ``sandbox`` without ``allow-same-origin`` leaves the
#: parent no other channel -- and "it never ran" would otherwise look like "it answered no".
_INNER = ("<!doctype html><body><scr" + "ipt>parent.postMessage({probe: true,"
          " secure: window.isSecureContext, url: document.URL,"
          " members: [" + ", ".join(f"typeof navigator.{name}" for name in MEMBERS) + "]"
          "}, '*')</scr" + "ipt></body>")

PAGE = """<!doctype html><body><script>
var out = {}; window.__out = out;
window.addEventListener('message', function (event) {
    if (event.data && event.data.probe) {
        out.sandboxed = event.data;
        out.answer = [event.data.secure, event.data.members.join(',')];
        window.__done = true;
    }
});
var box = document.createElement('iframe');
box.setAttribute('sandbox', 'allow-scripts');
box.srcdoc = __INNER__;
document.body.appendChild(box);
var plain = document.createElement('iframe');
document.body.appendChild(plain);
setTimeout(function () {
    try {
        var view = plain.contentWindow;
        out.plain = [view.isSecureContext, __MEMBERS__.map(function (name) {
            return typeof view.navigator[name]; }).join(',')];
    } catch (error) { out.plain = 'threw: ' + String(error); }
}, 300);
</script></body>""".replace("__INNER__", json.dumps(_INNER).replace("</", "<\\/")) \
               .replace("__MEMBERS__", json.dumps(MEMBERS))


@pytest.mark.parametrize("url, secure", [("https://frames.test/", True),
                                         ("http://frames.test/", False)])
def test_a_frameless_document_inherits_the_context_of_its_creator(url, secure):
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content(PAGE, url=url)
        _pump(page)
        seen = json.loads(page.evaluate("JSON.stringify(window.__out || {})"))
        assert seen.get("url", "about:srcdoc") == "about:srcdoc", seen
        expected = "object" if secure else "undefined"
        assert seen.get("answer") == [secure, ",".join([expected] * len(MEMBERS))], seen
        assert seen.get("plain") == [secure, ",".join([expected] * len(MEMBERS))], seen
    finally:
        _bounded(browser.close)
