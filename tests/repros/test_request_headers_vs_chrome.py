"""The headers pyv8c sends, compared with the ones the local Chrome sends.

Both sides fetch the same local page and run the same script, and the servers record
what arrived.  Each header Chrome sent has to be there with the same value (the user
agent excepted: the reference is headless) and in the same order, once ``Host`` and
the headers the transport places itself are removed.  A missing Chrome or playwright
skips the module.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import re
import threading
from urllib.parse import urlparse

import pytest

pytest.importorskip("pyv8", reason="the comparison runs in the native environment")

from harness import ReferenceUnavailable, chrome_reference

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
PAGE = """<!doctype html><html><head><meta charset=utf-8><title>probe</title>
<link rel=stylesheet href="/sub.css"></head><body>
<img src="/img.png"><script src="/sub.js"></script>
<video src="/clip.mp4" muted></video>
<iframe src="/frame.html"></iframe>
</body></html>"""

PROBE = """(async () => {
  await fetch('/js/fetch-get');
  await fetch('/js/fetch-post', {method: 'POST',
    headers: {'content-type': 'application/json'}, body: '{"a":1}'});
  try { await fetch('%(cross)s/js/cross-simple', {mode: 'cors'}); } catch (e) {}
  await new Promise((r) => {
    const x = new XMLHttpRequest(); x.open('GET', '/js/xhr-get');
    x.onloadend = r; x.onerror = r; x.send();
  });
  return 'done';
})()"""

#: Headers the transport places itself; their position is not ours to decide.
TRANSPORT_PLACED = {"host", "connection", "content-length"}

RECORDS: list[dict] = []
LOCK = threading.Lock()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        with LOCK:
            RECORDS.append({
                "method": self.command,
                "path": urlparse(self.path).path,
                "headers": list(self.headers.items()),
            })
        payload, content_type = {
            "/page": (PAGE.encode(), "text/html; charset=utf-8"),
            "/frame.html": (b"<html><body>frame</body></html>", "text/html; charset=utf-8"),
            "/sub.css": (b"body{color:red}", "text/css"),
            "/sub.js": (b"console.log(1)", "application/javascript"),
            "/img.png": (PNG, "image/png"),
        }.get(self.path, (b"{}", "application/json"))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def _collect(records):
    """``(method, path) -> {name: value}`` in wire order."""
    collected = {}
    for record in records:
        collected.setdefault(
            (record["method"], record["path"]),
            {name.lower(): value for name, value in record["headers"]},
        )
    return collected


def _normalized(headers):
    """Chrome's headless user agent only differs by the token naming the mode."""
    return {name: re.sub(r"HeadlessChrome", "Chrome", value) for name, value in headers.items()}


@pytest.fixture(scope="module")
def reference():
    """One local Chrome for the module, or a skip saying what is missing."""
    try:
        with chrome_reference() as session:
            yield session
    except ReferenceUnavailable as unavailable:
        pytest.skip(str(unavailable))


@pytest.fixture(scope="module")
def compared(reference):
    """The same page and script in both browsers; both sides' servers are here."""
    servers = []
    for _ in range(2):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    same_origin = f"http://127.0.0.1:{servers[0].server_port}"
    cross_origin = f"http://127.0.0.1:{servers[1].server_port}"
    url = f"{same_origin}/page"
    script = PROBE % {"cross": cross_origin}
    try:
        with LOCK:
            RECORDS.clear()
        chrome_page = reference.browser.new_page()
        try:
            chrome_page.goto(url, wait_until="load")
            chrome_page.evaluate(script)
            chrome_page.wait_for_timeout(500)
        finally:
            chrome_page.close()
        with LOCK:
            chrome = _collect(list(RECORDS))

        from pyv8c import Browser

        with LOCK:
            RECORDS.clear()
        browser = Browser(real_network=True, stub_logging="off")
        try:
            page = browser.new_context().new_page()
            page.goto(url, wait_until="load")
            page.evaluate(script)
            page.run_until_idle(timeout=5)
        finally:
            browser.close()
        with LOCK:
            mine = _collect(list(RECORDS))
        yield {"chrome": chrome, "pyv8c": mine}
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_every_request_the_browser_makes_is_made_here(compared):
    missing = sorted(set(compared["chrome"]) - set(compared["pyv8c"]))
    assert missing == [("GET", "/favicon.ico")], (
        "only the favicon may be absent; Chrome sent " + repr(missing)
    )


def test_each_request_carries_the_same_headers_with_the_same_values(compared):
    for key, reference_headers in sorted(compared["chrome"].items()):
        if key not in compared["pyv8c"]:
            continue
        theirs = _normalized(reference_headers)
        ours = compared["pyv8c"][key]
        assert set(ours) == set(theirs), f"{key}: header names differ"
        for name, value in theirs.items():
            assert ours[name] == value, f"{key}: {name}"


def test_the_order_agrees_once_the_transports_own_are_removed(compared):
    for key, reference_headers in sorted(compared["chrome"].items()):
        if key not in compared["pyv8c"]:
            continue
        theirs = [name for name in reference_headers if name not in TRANSPORT_PLACED]
        ours = [name for name in compared["pyv8c"][key] if name not in TRANSPORT_PLACED]
        assert ours == theirs, f"{key}: order differs"


def test_the_transport_placed_headers_are_still_sent(compared):
    """``Host``, ``Connection`` and ``Content-Length`` may move, not disappear."""
    for key, reference_headers in sorted(compared["chrome"].items()):
        if key not in compared["pyv8c"]:
            continue
        ours = compared["pyv8c"][key]
        for name in TRANSPORT_PLACED & set(reference_headers):
            assert name in ours, f"{key}: {name} is missing"
