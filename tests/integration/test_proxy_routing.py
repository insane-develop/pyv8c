"""Traffic leaving through a proxy, and the traffic that must not.

Every assertion here is wire-level: a real proxy (``proxy_harness``) and a real
origin are started, and what a context did is read back from what the proxy was
asked to carry.  That is the only way to tell "went through the proxy" from
"went direct and happened to work", and the second half of the file is about the
latter: an unconfigured browser must not use a proxy even when the environment
names one.

The websocket side needs its own waiter: an open socket is not pending work, so
a message arriving on one cannot be waited for by an idle wait alone (the same
reason ``tests/integration/test_websocket.py`` has one).
"""
import base64
import http.server
import json
import threading
import time

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from proxy_harness import RecordingProxy
from pyv8c import Browser, DOMException, register_transport

PAGE = """<!doctype html><html><body>
<img id=pixel src="/pixel.png">
</body></html>"""


class _OriginHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.hits.append(self.path)
        if self.path == "/page":
            body, content_type = PAGE.encode(), "text/html"
        elif self.path == "/pixel.png":
            body, content_type = b"\x89PNG\r\n\x1a\n", "image/png"
        elif self.path == "/slow":
            time.sleep(0.2)
            body, content_type = b"slow", "text/plain"
        else:
            body, content_type = b'{"answer": 42}', "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _Origin(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _OriginHandler)
        self.hits = []


@pytest.fixture
def origin():
    """A plain HTTP origin, with the paths it served recorded."""
    server = _Origin()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server.hits
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def proxy():
    """A proxy that carries the traffic and writes down what it carried."""
    server = RecordingProxy()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def echo():
    """A WebSocket echo server, reached through the tunnel."""
    from websockets.sync.server import serve

    def handler(connection):
        for message in connection:
            connection.send(message)

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"ws://127.0.0.1:{port}/echo"
    finally:
        server.shutdown()


def wait_for(page, expression, timeout=10):
    """Turn the page's loop until *expression* holds, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page.run_until_idle(timeout=0.1)
        if page.evaluate(expression):
            return True
    return False


def carried(proxy, fragment):
    """The request lines the proxy carried that mention *fragment*."""
    return [line for line in proxy.carried() if fragment in line]


# -- traffic that goes through ------------------------------------------------


def test_a_host_request_goes_through_the_proxy(proxy, origin):
    url, hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        response = browser.new_context().new_page().net.get(f"{url}/data")
        assert response.status == 200
        assert json.loads(response.body) == {"answer": 42}
    finally:
        browser.close()
    assert carried(proxy, "/data"), proxy.carried()
    assert hits == ["/data"]


def test_a_navigation_goes_through_the_proxy(proxy, origin):
    url, hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        page = browser.new_context().new_page()
        page.goto(f"{url}/page", wait_until="load")
        assert page.evaluate("document.querySelector('#pixel') !== null")
    finally:
        browser.close()
    assert carried(proxy, "/page"), proxy.carried()
    assert "/page" in hits


def test_a_subresource_goes_through_the_proxy(proxy, origin):
    url, _hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        browser.new_context().new_page().goto(f"{url}/page", wait_until="load")
    finally:
        browser.close()
    assert carried(proxy, "/pixel.png"), proxy.carried()


def test_a_page_fetch_goes_through_the_proxy(proxy, origin):
    url, _hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url=f"{url}/page")
        page.evaluate("globalThis.answer = null")
        page.evaluate("fetch('/data').then(r => r.json()).then(d => answer = d.answer)")
        assert wait_for(page, "answer !== null"), page.evaluate("answer")
        assert page.evaluate("answer") == 42
    finally:
        browser.close()
    assert carried(proxy, "/data"), proxy.carried()


def test_xhr_goes_through_the_proxy(proxy, origin):
    url, _hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url=f"{url}/page")
        page.evaluate("""
            globalThis.answer = null;
            const xhr = new XMLHttpRequest();
            xhr.open('GET', '/data');
            xhr.onload = () => { answer = JSON.parse(xhr.responseText).answer; };
            xhr.send();
        """)
        assert wait_for(page, "answer !== null")
        assert page.evaluate("answer") == 42
    finally:
        browser.close()
    assert carried(proxy, "/data"), proxy.carried()


def test_a_websocket_handshake_is_tunnelled_through_the_proxy(proxy, echo):
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        page.evaluate(f"""
            globalThis.said = null;
            const socket = new WebSocket('{echo}');
            socket.onopen = () => socket.send('hello');
            socket.onmessage = (event) => said = event.data;
        """)
        assert wait_for(page, "said !== null"), page.evaluate("said")
        assert page.evaluate("said") == "hello"
    finally:
        browser.close()
    assert proxy.tunnels, proxy.requests
    assert proxy.tunnels[0].startswith("127.0.0.1:")


def test_credentials_in_the_url_are_sent_to_the_proxy(proxy, origin):
    url, _hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.authenticated_url)
    try:
        browser.new_context().new_page().net.get(f"{url}/data")
    finally:
        browser.close()
    assert proxy.authorizations, proxy.carried()
    scheme, _, payload = proxy.authorizations[0].partition(" ")
    assert scheme.lower() == "basic"
    assert base64.b64decode(payload).decode() == "user:secret"


def test_credentials_reach_the_proxy_on_a_tunnel(proxy, echo):
    browser = Browser(stub_logging="off", proxy=proxy.authenticated_url)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        page.evaluate(f"""
            globalThis.state = 'connecting';
            const socket = new WebSocket('{echo}');
            socket.onopen = () => state = 'open';
            socket.onclose = () => state = 'closed';
        """)
        assert wait_for(page, "state === 'open'"), page.evaluate("state")
    finally:
        browser.close()
    assert proxy.authorizations, proxy.requests
    payload = proxy.authorizations[0].partition(" ")[2]
    assert base64.b64decode(payload).decode() == "user:secret"


def test_https_traffic_is_tunnelled_rather_than_dropped(proxy, origin):
    """An https request reaches the proxy as ``CONNECT``, and it is refused here."""
    refuser = RecordingProxy(refuse_connect=True)
    try:
        browser = Browser(stub_logging="off", proxy=refuser.url)
        try:
            page = browser.new_context().new_page()
            # The tunnel is refused, so the request cannot succeed -- which is
            # the point: it went to the proxy rather than straight out.
            from pyv8c.foundation.host_errors import LoadFailure

            with pytest.raises(LoadFailure):
                page.net.get("https://127.0.0.1:8443/data")
        finally:
            browser.close()
    finally:
        refuser.close()
    assert refuser.refused == ["127.0.0.1:8443"]
    assert origin[1] == []


# -- contexts with their own exits --------------------------------------------


def test_contexts_of_one_browser_leave_by_different_exits(proxy, origin):
    url, hits = origin
    browser = Browser(stub_logging="off")
    try:
        through = browser.new_context(proxy=proxy.url)
        direct = browser.new_context()
        through.new_page().net.get(f"{url}/data")
        direct.new_page().net.get(f"{url}/data")
        assert through.proxy == proxy.url
        assert direct.proxy is None
    finally:
        browser.close()
    assert len(carried(proxy, "/data")) == 1, proxy.carried()
    assert hits == ["/data", "/data"]


def test_a_context_can_go_direct_while_the_browser_has_a_proxy(proxy, origin):
    url, hits = origin
    browser = Browser(stub_logging="off", proxy=proxy.url)
    try:
        direct = browser.new_context(proxy="")
        assert direct.proxy == ""
        direct.new_page().net.get(f"{url}/data")
    finally:
        browser.close()
    assert proxy.carried() == []
    assert hits == ["/data"]


def test_a_context_can_be_given_a_proxy_the_browser_does_not_have(proxy, origin):
    url, hits = origin
    browser = Browser(stub_logging="off", proxy="")
    try:
        through = browser.new_context(proxy=proxy.url)
        through.new_page().net.get(f"{url}/data")
    finally:
        browser.close()
    assert carried(proxy, "/data"), proxy.carried()
    assert hits == ["/data"]


# -- traffic that must not go through ----------------------------------------


def test_an_unconfigured_browser_ignores_the_environment_proxy(proxy, origin, monkeypatch):
    url, hits = origin
    monkeypatch.setenv("HTTP_PROXY", proxy.url)
    monkeypatch.setenv("HTTPS_PROXY", proxy.url)
    monkeypatch.setenv("ALL_PROXY", proxy.url)
    monkeypatch.setenv("http_proxy", proxy.url)
    browser = Browser(stub_logging="off")
    try:
        response = browser.new_context().new_page().net.get(f"{url}/data")
        assert response.status == 200
    finally:
        browser.close()
    assert proxy.carried() == []
    assert hits == ["/data"]


def test_an_unconfigured_browser_opens_a_websocket_directly(proxy, echo, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", proxy.url)
    monkeypatch.setenv("ALL_PROXY", proxy.url)
    monkeypatch.setenv("http_proxy", proxy.url)
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        page.evaluate(f"""
            globalThis.said = null;
            const socket = new WebSocket('{echo}');
            socket.onopen = () => socket.send('direct');
            socket.onmessage = (event) => said = event.data;
        """)
        assert wait_for(page, "said !== null"), page.evaluate("said")
        assert page.evaluate("said") == "direct"
    finally:
        browser.close()
    assert proxy.tunnels == [] and proxy.carried() == []


def test_a_resource_loader_answers_before_the_proxy_is_reached(proxy, origin):
    url, hits = origin

    def loader(target, request):
        return {"body": "<body>loaded</body>", "headers": {"content-type": "text/html"}}

    browser = Browser(stub_logging="off", proxy=proxy.url, resource_loader=loader)
    try:
        page = browser.new_context().new_page()
        page.goto(f"{url}/page", wait_until="load")
        assert page.evaluate("document.body.textContent") == "loaded"
    finally:
        browser.close()
    assert proxy.carried() == []
    assert hits == []


def test_an_interceptor_that_answers_never_reaches_the_proxy(proxy, origin):
    url, hits = origin

    def interceptor(target, request):
        return {"body": "<body>intercepted</body>", "headers": {"content-type": "text/html"}}

    browser = Browser(stub_logging="off", proxy=proxy.url, resource_interceptor=interceptor)
    try:
        page = browser.new_context().new_page()
        page.goto(f"{url}/page", wait_until="load")
        assert page.evaluate("document.body.textContent") == "intercepted"
    finally:
        browser.close()
    # The interceptor answers before the transport, so the proxy never hears
    # about the load -- the same position the resource loader answers from.
    assert proxy.carried() == []
    assert hits == []


# -- configuration mistakes ----------------------------------------------------


def test_an_invalid_proxy_is_refused_when_it_is_given(origin):
    url, _hits = origin
    with pytest.raises(ValueError, match="SOCKS"):
        Browser(stub_logging="off", proxy="socks5://127.0.0.1:1080")
    with pytest.raises(ValueError, match="needs a scheme"):
        Browser(stub_logging="off", proxy="127.0.0.1:3128")
    with pytest.raises(TypeError, match="proxy must be"):
        Browser(stub_logging="off", proxy=3128)


def test_a_context_override_is_validated_too():
    browser = Browser(stub_logging="off")
    try:
        with pytest.raises(ValueError, match="SOCKS"):
            browser.new_context(proxy="socks4://127.0.0.1:1080")
    finally:
        browser.close()


def test_a_proxy_without_an_engine_is_refused():
    browser = Browser(stub_logging="off", real_network=False, proxy="http://127.0.0.1:3128")
    try:
        with pytest.raises(TypeError, match="no network engine"):
            browser.new_context()
    finally:
        browser.close()


def test_an_engine_that_cannot_be_proxied_is_refused():
    class Silent:
        def request_sync(self, request):
            raise NotImplementedError

        def close(self):
            return None

    register_transport("proxy-refusing-probe", lambda *, options=None: Silent(),
                       option_keys=lambda: frozenset())
    browser = Browser(stub_logging="off", http_engine="proxy-refusing-probe",
                      proxy="http://127.0.0.1:3128")
    try:
        with pytest.raises(TypeError, match="cannot be sent through a proxy"):
            browser.new_context()
    finally:
        browser.close()


def test_cronet_carries_its_traffic_through_the_proxy(proxy, origin):
    """cycronet is handed the context's proxy at construction, so its loads leave by it."""
    url, _hits = origin
    browser = Browser(stub_logging="off", http_engine="cronet", proxy=proxy.url)
    try:
        page = browser.new_context().new_page()
        page.goto(f"{url}/page", wait_until="load")
        assert page.evaluate("document.querySelector('#pixel') !== null")
    finally:
        browser.close()
    carried = proxy.carried()
    assert carried and all(url in line for line in carried)


def test_a_proxy_and_the_engines_own_proxy_option_are_refused_together():
    browser = Browser(
        stub_logging="off",
        http_options={"proxies": "http://127.0.0.1:9"},
        proxy="http://127.0.0.1:3128",
    )
    try:
        with pytest.raises(TypeError, match="keep one"):
            browser.new_context()
    finally:
        browser.close()
