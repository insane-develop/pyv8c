"""``new WebSocket`` on the real ``websockets`` engine.

The unit tests drive the page's socket API with a fixture engine; these run a
real server, because what a socket engine is for is exactly what a fixture would
hide: the handshake a browser sends, the frames that cross the wire, and the
close handshake both sides have to agree on.
"""
import threading
import time

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytest.importorskip("websockets", reason="the default socket engine needs websockets")

from websockets.sync.server import serve

from pyv8c import Browser


def document(url, request):
    """Serve the page itself; the sockets are the point of these tests."""
    return {"body": "<body></body>", "headers": {"content-type": "text/html"}}


def wait_for(page, expression, timeout=5):
    """Turn the page's loop until *expression* holds, or give up.

    An open socket is not pending work, so a message arriving on one cannot be
    waited for by an idle wait alone.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page.run_until_idle(timeout=0.1)
        if page.evaluate(expression):
            return True
    return False


class EchoServer:
    """A WebSocket server that echoes, and remembers what it was asked."""

    def __init__(self, subprotocols=None):
        self.handshakes = []
        self.closed = []
        self._server = serve(self._handler, "127.0.0.1", 0, subprotocols=subprotocols)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/socket"

    def _serve(self):
        try:
            self._server.serve_forever()
        except Exception:  # shutdown closes the socket underneath it
            pass

    def _handler(self, connection):
        self.handshakes.append(dict(connection.request.headers))
        try:
            for message in connection:
                connection.send(message)
        finally:
            self.closed.append(connection.close_code)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._thread.join(timeout=5)


@pytest.fixture
def echo():
    with EchoServer() as server:
        yield server


def test_a_page_socket_talks_to_a_real_server(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_context().new_page("http://example.test/")
        page.evaluate(f"""
            window.__events = [];
            const ws = new WebSocket('{echo.url}');
            ws.onopen = () => {{ window.__events.push(['open', ws.protocol]); ws.send('hello'); }};
            ws.onmessage = e => {{ window.__events.push(['message', e.data, typeof e.data]); ws.close(1000, 'done'); }};
            ws.onclose = e => window.__events.push(['close', e.code, e.reason, e.wasClean]);
            window.__ws = ws;
        """)
        assert wait_for(page, "__events.length >= 3"), page.evaluate("__events")
        assert page.evaluate("__events") == [
            ["open", ""], ["message", "hello", "string"], ["close", 1000, "done", True],
        ]
        assert page.evaluate("__ws.readyState") == 3
    finally:
        browser.close()


def test_binary_frames_survive_the_round_trip(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_context().new_page("http://example.test/")
        # A Blob built from a string carries its bytes; a Blob or a send built
        # from a bare typed array does not -- ``bytes_from_value`` falls through
        # to ``bytes(value)``, which iterates a JavaScript object: its indices,
        # not its elements.  That defect is the Blob constructor's, not this
        # engine's, and it is not what these tests are about.
        page.evaluate(f"""
            window.__events = [];
            const ws = new WebSocket('{echo.url}');
            ws.binaryType = 'arraybuffer';
            ws.onopen = () => ws.send(new Blob(['abc']));
            ws.onmessage = e => window.__events.push([e.data instanceof ArrayBuffer, Array.from(new Uint8Array(e.data))]);
            window.__ws = ws;
        """)
        assert wait_for(page, "__events.length >= 1"), page.evaluate("__events")
        assert page.evaluate("__events") == [[True, [97, 98, 99]]]
    finally:
        browser.close()


def test_a_handshake_carries_the_browser_headers_and_the_jar(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        context = browser.new_context()
        page = context.new_page("http://example.test/")
        # A page sets cookies for its own host only, so the jar is filled the way
        # a response from the socket's host would have filled it.
        context.cookies.set_from_document(echo.url.replace("ws://", "http://"), "sid=1; path=/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
        headers = echo.handshakes[-1]
        # A browser's handshake, not Python's: the document's origin, the
        # environment's headers and the context's cookies for the target.
        assert headers["origin"] == "http://example.test"
        assert headers["user-agent"].startswith("Mozilla/5.0")
        assert "zh-CN" in headers["accept-language"]
        assert headers["cookie"] == "sid=1"
    finally:
        browser.close()


def test_the_handshake_can_leave_the_cookies_behind(echo):
    browser = Browser(
        ws_engine="websockets", ws_options={"cookies": False}, resource_loader=document,
    )
    try:
        context = browser.new_context()
        page = context.new_page("http://example.test/")
        context.cookies.set_from_document(echo.url.replace("ws://", "http://"), "sid=1; path=/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
        assert "cookie" not in echo.handshakes[-1]
    finally:
        browser.close()


def test_extra_handshake_headers_come_from_the_options(echo):
    browser = Browser(
        ws_engine="websockets", ws_options={"headers": {"X-Page": "socket"}}, resource_loader=document,
    )
    try:
        page = browser.new_context().new_page("http://example.test/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
        assert echo.handshakes[-1]["x-page"] == "socket"
    finally:
        browser.close()


def test_a_subprotocol_is_negotiated():
    with EchoServer(subprotocols=["chat"]) as server:
        browser = Browser(ws_engine="websockets", resource_loader=document)
        try:
            page = browser.new_context().new_page("http://example.test/")
            page.evaluate(f"window.__ws = new WebSocket('{server.url}', ['chat'])")
            assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
            assert page.evaluate("__ws.protocol") == "chat"
        finally:
            browser.close()


def test_a_socket_that_cannot_connect_reports_error_then_close():
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_context().new_page("http://example.test/")
        page.evaluate("""
            window.__events = [];
            const ws = new WebSocket('ws://127.0.0.1:1/');
            ws.onerror = () => window.__events.push('error');
            ws.onclose = e => window.__events.push(['close', e.code, e.wasClean]);
            window.__ws = ws;
        """)
        assert wait_for(page, "__events.length >= 2"), page.evaluate("__events")
        assert page.evaluate("__events") == ["error", ["close", 1006, False]]
    finally:
        browser.close()


def test_closing_the_context_closes_the_socket(echo):
    browser = Browser(ws_engine="websockets", resource_loader=document)
    try:
        page = browser.new_context().new_page("http://example.test/")
        page.evaluate(f"window.__ws = new WebSocket('{echo.url}')")
        assert wait_for(page, "__ws.readyState === 1"), page.evaluate("__ws.readyState")
    finally:
        browser.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not echo.closed:
        time.sleep(0.05)
    # The engine's own closing handshake, not a dropped socket.
    assert echo.closed == [1000]
