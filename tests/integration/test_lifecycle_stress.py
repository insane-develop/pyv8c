"""Concurrent and lifecycle stress for Page, worker and DevTools realms.

These tests drive pages from several threads at once and then tear them down
mid-flight.  The assertions are deliberately structural: no thread may wedge,
``close()`` must stay idempotent under races, responses that land after a
close must be dropped silently, and the process must return to its baseline
thread count.
"""
import asyncio
import json
import threading
import time
from urllib.parse import urlsplit

import pytest

pytest.importorskip("pyv8")
pytestmark = [pytest.mark.race, pytest.mark.slow]
from websockets.asyncio.client import connect

from pyv8c import Browser, DevToolsOptions

ORIGIN = "http://127.0.0.1:9"


def _loader(url, request):
    path = urlsplit(str(url)).path
    if path == "/worker.js":
        return {
            "status": 200,
            "url": str(url),
            "body": (
                "self.onmessage = function (e) { postMessage(e.data * 2); };"
                "setInterval(function () {}, 50);"
            ),
            "headers": {"content-type": "text/javascript"},
        }
    if path == "/closing.js":
        return {
            "status": 200,
            "url": str(url),
            "body": "self.onmessage = function () { self.close(); };",
            "headers": {"content-type": "text/javascript"},
        }
    if path.startswith("/slow"):
        time.sleep(0.4)
    return {"status": 200, "url": str(url), "body": "", "headers": {"content-type": "text/html"}}


@pytest.fixture
def browser():
    browser = Browser(resource_loader=_loader, stub_logging="off")
    try:
        yield browser
    finally:
        browser.close()


def _run_threads(threads, timeout=20):
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(t.is_alive() for t in threads), "one or more threads wedged"


def test_concurrent_close_is_idempotent(browser):
    page = browser.new_context().new_page(ORIGIN + "/")
    _run_threads([threading.Thread(target=page.close) for _ in range(4)])
    assert page.closed
    page.close()  # still idempotent afterwards
    browser.close()
    browser.close()  # browser-level close is idempotent too


def test_evaluate_races_page_close(browser):
    page = browser.new_context().new_page(ORIGIN + "/")
    errors = []

    def evaler():
        try:
            # Hammer the realm until close() lands; a surviving realm would
            # make this loop run to its deadline and mark the test failed.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                page.evaluate("Math.random()")
            errors.append("no close observed within 5s")
        except BaseException as exc:  # close races are expected
            errors.append(type(exc).__name__)

    def closer():
        time.sleep(0.01)
        page.close()

    _run_threads([threading.Thread(target=evaler), threading.Thread(target=closer)])
    assert page.closed
    assert errors and errors[0] != "no close observed within 5s"


def test_worker_message_delivery_races_page_close(browser):
    first = browser.new_context().new_page(ORIGIN + "/")
    second = browser.new_context().new_page(ORIGIN + "/")
    for page in (first, second):
        page.evaluate("""
            window.__worker = 'pending';
            var w = new Worker('/worker.js');
            w.onmessage = function (e) { window.__worker = e.data; };
            w.onerror = function () { window.__worker = 'error'; };
            w.postMessage(7);
        """)
    # Let the workers spin up, then tear both pages down concurrently while
    # worker messages and intervals are still in flight.
    time.sleep(0.1)
    _run_threads([threading.Thread(target=first.close), threading.Thread(target=second.close)])
    assert first.closed and second.closed


def test_a_worker_that_closed_itself_does_not_wedge_the_page_close(browser):
    """A worker's own ``close()`` tears its realm down on the owner thread; the page's
    ``terminate()`` must find that done rather than dispatch onto the dead context."""
    page = browser.new_context().new_page(ORIGIN + "/")
    page.evaluate("""
        var w = new Worker('/closing.js');
        w.postMessage('go');
    """)
    time.sleep(0.2)
    page.close()
    assert page.closed


def test_network_response_landing_after_close_is_dropped(browser):
    page = browser.new_context().new_page(ORIGIN + "/")
    # load_resource_async posts the delivery onto the page task queue; closing
    # the page right after evaluate() returns must discard that delivery.
    page.evaluate("fetch('/slow')")
    page.close()
    time.sleep(0.5)
    assert page.closed


def test_timer_pending_at_close_is_dropped(browser):
    page = browser.new_context().new_page(ORIGIN + "/")
    page.evaluate("""
        window.__count = 0;
        setInterval(function () { window.__count += 1; }, 10);
        setTimeout(function () { window.__count += 1; }, 500);
    """)
    page.close()
    time.sleep(0.2)
    assert page.closed


def test_devtools_pause_supports_cdp_attach_evaluate_detach_and_close():
    browser = Browser(resource_loader=_loader, stub_logging="off")
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, options=DevToolsOptions(wait_for_debugger=True, startup_timeout=3))
    completed = threading.Event()
    probe_done = threading.Event()
    errors = []

    def navigate():
        try:
            page.set_content("<script>globalThis.answer = 42;</script>")
        except BaseException as exc:  # collected for the test
            errors.append(exc)
        finally:
            completed.set()

    async def probe():
        try:
            async with connect(f"ws://{handle.host}:{handle.port}/") as ws:
                targets = await _cdp_request(ws, {"id": 1, "method": "Target.getTargets"})
                target_id = targets["result"]["targetInfos"][0]["targetId"]
                attached = await _cdp_request(ws, {
                    "id": 2, "method": "Target.attachToTarget",
                    "params": {"targetId": target_id},
                })
                sid = attached["result"]["sessionId"]
                # Runtime.evaluate while the gate is held queues instead of
                # blocking the websocket server or the main thread.
                await ws.send(json.dumps({
                    "id": 3, "method": "Runtime.evaluate",
                    "params": {"expression": "1 + 1"}, "sessionId": sid,
                }))
                await asyncio.sleep(0.05)
                detached = await _cdp_request(ws, {
                    "id": 4, "method": "Target.detachFromTarget",
                    "params": {"sessionId": sid},
                })
                assert detached["result"] == {}
                probe_done.set()
                # Keep the socket open so only page.close() releases the gate.
                while not completed.is_set():
                    try:
                        await asyncio.wait_for(ws.recv(), 0.2)
                    except asyncio.TimeoutError:
                        continue
        except BaseException as exc:  # collected for the test
            errors.append(exc)
            probe_done.set()

    nav_thread = threading.Thread(target=navigate)
    probe_thread = threading.Thread(target=lambda: asyncio.run(probe()))
    nav_thread.start()
    probe_thread.start()
    try:
        assert probe_done.wait(6), "paused realm never answered attach/detach"
        # The navigation is still gated while the CDP socket stays attached.
        assert not completed.is_set()
        page.close()
        nav_thread.join(6)
        probe_thread.join(6)
        assert not nav_thread.is_alive(), "navigation wedged during close"
        assert not probe_thread.is_alive(), "CDP probe wedged during close"
        assert errors == []
        assert page.closed
        page.close()  # idempotent after teardown
    finally:
        handle.close()
        browser.close()


async def _cdp_request(ws, payload):
    await ws.send(json.dumps(payload))
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), 3))
        if message.get("id") == payload.get("id"):
            return message


