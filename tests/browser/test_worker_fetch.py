"""A worker fetches for itself: ``fetch`` exists there, and its response is readable.

A worker realm has no ``fetch`` of its own at all in this build before, which a challenge that
fingerprints from a worker notices at once; and a response built by the page's pipeline carries
the *page's* realm services, so reading its body inside the worker used to fail with "JavaScript
value belongs to another isolate".  Both halves are what this test holds down.
"""
import json
import threading
import time

from pyv8c import Browser

SOURCE = ("fetch('https://worker.test/answer.json').then(function (response) {"
          " self.postMessage('status:' + response.status);"
          " return response.text();"
          "}).then(function (text) { self.postMessage('body:' + text); })"
          ".catch(function (error) { self.postMessage('failed:' + error.name); });")

PAGE_HTML = """<!doctype html><body><script>
var out = {}; window.__out = out;
out.seen = [];
var worker = new Worker(URL.createObjectURL(new Blob([__SOURCE__],
  {type: 'text/javascript'})));
worker.onmessage = function (event) { out.seen.push(String(event.data)); };
worker.onerror = function (event) { out.seen.push('onerror:' + String(event.message || event)); };
</script></body>""".replace("__SOURCE__", json.dumps(SOURCE).replace("</", "<\\/"))

PROBE_URL = "https://worker.test/"


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
    """The probe page, and the JSON a worker inside it asks for."""
    if str(url).endswith("answer.json"):
        return {"body": "from-the-answer", "headers": {"content-type": "application/json"}}
    return {"body": PAGE_HTML, "headers": {"content-type": "text/html"}}


def test_a_worker_fetches_and_reads_its_response():
    browser = Browser(stub_logging="off", threaded_loaders=True, real_network=False,
                      resource_loader=_load)
    try:
        page = browser.new_context().new_page()
        page.goto(PROBE_URL)
        _pump(page)
        seen = json.loads(page.evaluate("JSON.stringify(window.__out.seen)"))
        assert seen == ["status:200", "body:from-the-answer"], seen
    finally:
        _bounded(browser.close)
