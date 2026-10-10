"""Why a call on a replaced realm met a ``NoneType`` context, and what this probe settled.

``GPU.requestAdapter()`` raised ``AttributeError: 'NoneType' object has no attribute 'exec_js'``
in the 2026-10-06 run.  The chain, from the traceback and the code:

1. ``Page._renew_document_realm`` (``platform/page.py:273``) builds a new Window and realm for
   the new document, **reuses the page's executor and task queue**, then closes the previous
   realm through ``previous_realm.close_context()``;
2. ``platform/realms.py:266`` calls ``scripts.close()``, which sets ``_context = None`` -- so any
   helper that needs that V8 context (``promise_resolve``, ``js_error_value``) fails there;
3. the only work cancelled at a commit is the old document's **timers**
   (``navigation.py:59`` -> ``tasks.cancel_timers()``); the comment beside it says the queue
   belongs to the page and outlives the document.

**What this probe settled**: a slow ``fetch`` does *not* leave a continuation behind -- the
navigation cancels it, ``window.name`` stays ``start`` and no engine error appears.  So the late
call in the real run came from work the *document does not own*: the widget drives a **worker**
and a worker's reply is delivered through the page's queue, which survives the document (the two
errors also came from two different realms, ``[TOP]`` and ``[FRAME]``).  Reproducing that shape
is the next probe; the engine-side tolerance is already in place
(``foundation/scripts.py``, ``RealmScripts.closed``).

This file stays as the record of that negative result: the next probe has to use a worker reply.

    python script/late_document_work.py
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills"
                       / "pyv8c-challenge-parity" / "scripts"))

FIRST = "https://probe.test/first.html"
SECOND = "https://probe.test/second.html"
SLOW = "https://probe.test/slow.json"

PAGE = """<!doctype html><body>first<script>
window.name = 'start';
function report(what, outcome) { window.name = what + ':' + outcome; }
fetch(__SLOW__, {mode: 'cors'}).then(function () {
  // The turn that matters: this document has already been replaced.
  try {
    var adapter = navigator.gpu.requestAdapter();
    report('late-gpu', adapter && typeof adapter.then === 'function' ? 'promise' : 'value');
  } catch (error) { report('late-gpu', 'threw ' + error.name); }
  try {
    var offer = new RTCPeerConnection().createOffer();
    report('late-offer', offer && typeof offer.then === 'function' ? 'promise' : 'value');
  } catch (error) { report('late-offer', 'threw ' + error.name); }
  report('late-both', navigator.hardwareConcurrency ? 'alive' : 'alive');
}, function (error) { report('late-fetch', 'rejected ' + error.name); });
setTimeout(function () { location.href = __SECOND__; }, 30);
</script></body>"""

SECOND_PAGE = """<!doctype html><body>second<script>
document.title = 'second';
</script></body>"""


def main():
    from pyv8c import Browser

    def load(url, request):
        text = str(url)
        if text.startswith(SECOND):
            return {"body": SECOND_PAGE, "headers": {"content-type": "text/html"}}
        if text.startswith(SLOW):
            #: A response still in flight while the page navigates away: this is what leaves a
            #: continuation of the old document to run afterwards.
            time.sleep(1.2)
            return {"body": "{}", "headers": {"content-type": "application/json",
                                              "access-control-allow-origin": "*"}}
        if text.startswith(("http://", "https://")):
            return {"body": PAGE.replace("__SLOW__", repr(SLOW)).replace("__SECOND__", repr(SECOND)),
                    "headers": {"content-type": "text/html"}}
        return None

    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=load)
    try:
        page = browser.new_context().new_page()
        page.goto(FIRST)
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.05)
        print(f"  url      : {page.evaluate('String(location.pathname)')}")
        print(f"  window.name: {page.evaluate('String(window.name)')}")
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
