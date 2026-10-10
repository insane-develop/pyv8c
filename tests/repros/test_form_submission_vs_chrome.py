"""Form submissions compared with the local Chrome, on the wire.

The same pages, the same local server recording both browsers: the method, the
request line, the body bytes and the headers in arrival order have to agree.
Two things are normalized first -- a multipart boundary is random per
submission, and the reference's user agent names its headless mode.

A submission a page makes itself is driven by the served page and not by the
host, because a host call is a user gesture in either browser: that is what
``Sec-Fetch-User`` distinguishes, and driving it from the harness would hide
the difference instead of comparing it.
"""
from __future__ import annotations

import http.server
import re
import threading
from urllib.parse import urlparse

import pytest

pytest.importorskip("pyv8", reason="the comparison runs in the native environment")

from harness import ReferenceUnavailable, chrome_reference

FORM = ('<!doctype html><html><body><form method=post action="/echo">'
        '<p><input name=a value=1></p><p><button name=b value=bv>Go</button></p>'
        '</form>{script}</body></html>')
GET_FORM = ('<!doctype html><html><body><form method=get action="/echo?old=1">'
            '<p><input name=sp value="hello world"></p>'
            '<p><button name=b value="x y">Go</button></p></form></body></html>')
EMPTY_GET = ('<!doctype html><html><body><form method=get action="/echo?old=1">'
             '<p><button>Go</button></p></form></body></html>')
KINDS = ('<!doctype html><html><body><form method=post action="/echo">'
         '<p><input name=t value="v"></p>'
         '<p><input type=checkbox name=on checked></p>'
         '<p><input type=checkbox name=off></p>'
         '<p><input name=dis value="x" disabled></p>'
         '<p><input value="noname"></p>'
         '<p><select name=s><option value=o1 selected>1</option>'
         '<option value=o2>2</option></select></p>'
         '<p><textarea name=ta>text</textarea></p>'
         '<p><button name=b value=bv>Go</button></p></form></body></html>')
PLAIN_ENC = ('<!doctype html><html><body>'
             '<form method=post action="/echo" enctype="text/plain">'
             '<p><input name=a value="1"></p><p><input name=b value="2"></p>'
             '<p><button>Go</button></p></form></body></html>')
MULTIPART_ENC = ('<!doctype html><html><body>'
                 '<form method=post action="/echo" enctype="multipart/form-data">'
                 '<p><input name=a value="1"></p><p><button>Go</button></p>'
                 '</form></body></html>')

PAGES = {
    "/plain": FORM.format(script=""),
    "/script-submit": FORM.format(
        script='<script>document.querySelector("form").submit();</script>'),
    "/script-click": FORM.format(
        script='<script>document.querySelector("form button").click();</script>'),
    "/get": GET_FORM,
    "/empty-get": EMPTY_GET,
    "/kinds": KINDS,
    "/plain-enc": PLAIN_ENC,
    "/multi-enc": MULTIPART_ENC,
}

#: ``(name, path, who submits)``: a host click, or the page's own script.
CASES = (
    ("click", "/plain", "host"),
    ("script-submit", "/script-submit", "page"),
    ("script-click", "/script-click", "page"),
    ("get", "/get", "host"),
    ("empty-get", "/empty-get", "host"),
    ("kinds", "/kinds", "host"),
    ("text-plain", "/plain-enc", "host"),
    ("multipart", "/multi-enc", "host"),
)

#: The transport's own headers: their position is not the browser's to decide.
TRANSPORT_PLACED = {"host", "connection", "content-length"}
BOUNDARY = re.compile(r"----WebKitFormBoundary[A-Za-z0-9]{16}")

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
        body = self.rfile.read(length) if length else b""
        with LOCK:
            RECORDS.append({
                "method": self.command,
                "path": self.path,
                "headers": list(self.headers.items()),
                "body": body.decode("utf-8", "replace"),
            })
        payload = PAGES.get(urlparse(self.path).path, "").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def _normalized(record):
    """One recorded submission, with the two things the browsers choose removed."""
    headers = [(name.lower(), BOUNDARY.sub("<boundary>",
                                           re.sub(r"HeadlessChrome", "Chrome", value)))
               for name, value in record["headers"]]
    return {
        "method": record["method"],
        "path": record["path"],
        "headers": headers,
        "body": BOUNDARY.sub("<boundary>", record["body"]),
    }


def _taken(records):
    """The submission among *records*: the request for ``/echo``."""
    for record in records:
        if urlparse(record["path"]).path == "/echo":
            return _normalized(record)
    raise AssertionError(f"no submission was sent; got {[r['path'] for r in records]}")


@pytest.fixture(scope="module")
def compared():
    """Every case, submitted by both browsers against the same server."""
    try:
        with chrome_reference() as reference:
            yield _compare(reference)
    except ReferenceUnavailable as unavailable:
        pytest.skip(str(unavailable))


def _compare(reference):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        chrome = _chrome_submissions(reference, origin)

        from pyv8c import Browser

        browser = Browser(real_network=True, stub_logging="off")
        try:
            mine = _pyv8c_submissions(browser, origin)
        finally:
            browser.close()
        return {"chrome": chrome, "pyv8c": mine}
    finally:
        server.shutdown()
        server.server_close()


def _chrome_submissions(reference, origin):
    collected = {}
    for name, path, driver in CASES:
        with LOCK:
            RECORDS.clear()
        page = reference.browser.new_page()
        try:
            page.goto(f"{origin}{path}", wait_until="load")
            if driver == "host":
                page.click("form button")
            page.wait_for_timeout(500)
        finally:
            page.close()
        with LOCK:
            collected[name] = _taken(list(RECORDS))
    return collected


def _pyv8c_submissions(browser, origin):
    collected = {}
    page = browser.new_context().new_page()
    try:
        for name, path, driver in CASES:
            with LOCK:
                RECORDS.clear()
            page.goto(f"{origin}{path}", wait_until="load")
            if driver == "host":
                page.click("form button")
            page.run_until_idle(timeout=5)
            with LOCK:
                collected[name] = _taken(list(RECORDS))
    finally:
        page.close()
    return collected


def test_both_browsers_make_the_same_request_line(compared):
    for name in compared["chrome"]:
        theirs, ours = compared["chrome"][name], compared["pyv8c"][name]
        assert (ours["method"], ours["path"]) == (theirs["method"], theirs["path"]), name


def test_each_submission_carries_the_same_body(compared):
    for name in compared["chrome"]:
        assert compared["pyv8c"][name]["body"] == compared["chrome"][name]["body"], name


def test_each_submission_carries_the_same_headers_with_the_same_values(compared):
    for name in compared["chrome"]:
        theirs = dict(compared["chrome"][name]["headers"])
        ours = dict(compared["pyv8c"][name]["headers"])
        assert set(ours) == set(theirs), name
        for header, value in theirs.items():
            assert ours[header] == value, f"{name}: {header}"


def test_the_order_agrees_once_the_transports_own_are_removed(compared):
    for name in compared["chrome"]:
        theirs = [header for header, _ in compared["chrome"][name]["headers"]
                  if header not in TRANSPORT_PLACED]
        ours = [header for header, _ in compared["pyv8c"][name]["headers"]
                if header not in TRANSPORT_PLACED]
        assert ours == theirs, name
