"""What a form submission puts on the wire, held to Chrome 153's baseline.

Every expectation is a transcription of a request a local Chrome made for the
same page: the method, the request line, the body bytes and the headers in
arrival order.  A click comes from the host and so is a user activation, which
is the one thing a page's own ``element.click()`` is not.
"""

import http.server
import threading
from urllib.parse import urlparse

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytest.importorskip("curl_cffi", reason="the curl_cffi engine is not installed")
pytestmark = pytest.mark.native

from pyv8c import Browser

FORM = ('<!doctype html><html><body><form method=post action="/echo">'
        '<p><input name=a value=1></p><p><button name=b value=bv>Go</button></p>'
        '</form></body></html>')
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

PAGES = {"/plain": FORM, "/script": FORM, "/get": GET_FORM, "/empty-get": EMPTY_GET,
         "/kinds": KINDS, "/plain-enc": PLAIN_ENC, "/multi-enc": MULTIPART_ENC}

#: Chrome's order for a form POST; ``host``, ``content-length`` and
#: ``connection`` are the transport's to place (``docs/request-headers.md``).
POSTED_NAVIGATION = [
    "host", "cache-control", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "upgrade-insecure-requests", "content-type", "user-agent", "origin", "accept",
    "sec-fetch-site", "sec-fetch-mode", "sec-fetch-user", "sec-fetch-dest", "referer",
    "accept-encoding", "accept-language", "content-length", "connection",
]

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
                "body": body,
            })
        payload = PAGES.get(urlparse(self.path).path, "")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload.encode())))
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def submissions():
    """One submission per trigger, recorded as the server received it."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    browser = Browser(real_network=True, stub_logging="off")
    collected = {}
    try:
        page = browser.new_context().new_page()

        def submit(name, path, drive):
            with LOCK:
                RECORDS.clear()
            page.goto(f"{origin}{path}", wait_until="load")
            drive(page)
            page.run_until_idle(timeout=5)
            with LOCK:
                records = list(RECORDS)
            collected[name] = next((record for record in records
                                    if urlparse(record["path"]).path == "/echo"), None)
            collected[name + ".landed"] = page.url

        submit("click", "/plain", lambda page: page.click("form button"))
        submit("script", "/script",
               lambda page: page.evaluate("document.querySelector('form').submit()"))
        submit("untrusted-click", "/script",
               lambda page: page.evaluate("document.querySelector('form button').click()"))
        submit("get", "/get", lambda page: page.click("form button"))
        submit("empty-get", "/empty-get", lambda page: page.click("form button"))
        submit("kinds", "/kinds", lambda page: page.click("form button"))
        submit("text-plain", "/plain-enc", lambda page: page.click("form button"))
        submit("multipart", "/multi-enc", lambda page: page.click("form button"))
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
    yield collected


def headers_of(record):
    """The recorded headers lower-cased, in arrival order."""
    return {name.lower(): value for name, value in record["headers"]}


def test_a_clicked_submit_button_posts_the_entry_list(submissions):
    record = submissions["click"]
    assert (record["method"], record["path"]) == ("POST", "/echo")
    assert record["body"] == b"a=1&b=bv"
    sent = headers_of(record)
    assert list(sent) == POSTED_NAVIGATION
    assert sent["content-type"] == "application/x-www-form-urlencoded"
    assert sent["cache-control"] == "max-age=0"
    assert sent["sec-fetch-user"] == "?1"
    assert sent["sec-fetch-mode"] == "navigate"
    assert sent["sec-fetch-dest"] == "document"
    assert sent["upgrade-insecure-requests"] == "1"
    assert sent["origin"].startswith("http://127.0.0.1:")
    assert sent["referer"].endswith("/plain")


def test_a_script_submission_sends_no_submitter_and_no_user_activation(submissions):
    record = submissions["script"]
    assert (record["method"], record["path"]) == ("POST", "/echo")
    assert record["body"] == b"a=1"
    sent = headers_of(record)
    assert "sec-fetch-user" not in sent
    assert sent["origin"].startswith("http://127.0.0.1:")
    assert sent["cache-control"] == "max-age=0"
    assert sent["content-type"] == "application/x-www-form-urlencoded"


def test_an_untrusted_click_submits_its_button_without_user_activation(submissions):
    """A page's own ``button.click()`` sends the button's entry, but is no gesture."""
    record = submissions["untrusted-click"]
    assert record["body"] == b"a=1&b=bv"
    assert "sec-fetch-user" not in headers_of(record)


def test_a_get_submission_replaces_the_actions_query(submissions):
    record = submissions["get"]
    assert record["method"] == "GET"
    assert record["path"] == "/echo?sp=hello+world&b=x+y"
    assert record["body"] == b""
    assert submissions["get.landed"].endswith("/echo?sp=hello+world&b=x+y")
    sent = headers_of(record)
    assert "origin" not in sent
    assert "content-type" not in sent
    assert "cache-control" not in sent


def test_a_get_submission_with_no_entries_still_has_a_query(submissions):
    record = submissions["empty-get"]
    assert (record["method"], record["path"]) == ("GET", "/echo?")
    assert submissions["empty-get.landed"].endswith("/echo?")


def test_the_entry_list_leaves_out_what_the_browser_leaves_out(submissions):
    """Chrome sent ``t=v&on=on&s=o1&ta=text&b=bv`` for this page."""
    assert submissions["kinds"]["body"] == b"t=v&on=on&s=o1&ta=text&b=bv"


def test_text_plain_is_line_per_entry(submissions):
    record = submissions["text-plain"]
    assert record["body"] == b"a=1\r\nb=2\r\n"
    assert headers_of(record)["content-type"] == "text/plain"


def test_multipart_form_data_delimits_each_entry(submissions):
    record = submissions["multipart"]
    content_type = headers_of(record)["content-type"]
    prefix = "multipart/form-data; boundary="
    assert content_type.startswith(prefix)
    boundary = content_type[len(prefix):]
    assert record["body"] == (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="a"\r\n'
        f"\r\n1\r\n"
        f"--{boundary}--\r\n"
    ).encode()
