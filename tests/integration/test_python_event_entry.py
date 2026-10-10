"""Host-side event triggering: ``Page.dispatch_event`` and friends."""
import threading

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c.foundation.host_errors import HostStateError


@pytest.fixture
def host_page(page):
    page.set_content("<div id='app'><button id='go'>go</button></div>", url="https://example.test/")
    page.run_until_idle()
    page.evaluate("""
        window.__log = [];
        window.addEventListener('custom', e => __log.push(['window', e.detail, e.isTrusted, e.bubbles]));
        document.addEventListener('custom', e => __log.push(['document', e.detail]));
        document.addEventListener('click', e => __log.push(['doc-click', e.target.id, e.clientX, e.isTrusted, e.bubbles]));
        document.querySelector('#go').addEventListener('click', e => __log.push(['button', e.clientX]));
    """)
    return page


def test_dispatch_event_defaults_to_window(host_page):
    assert host_page.dispatch_event("custom", event_class="CustomEvent", init={"detail": 1}) is True
    assert host_page.evaluate("__log") == [["window", 1, True, False]]


def test_dispatch_event_to_document_and_selector(host_page):
    assert host_page.dispatch_event("custom", target="document", event_class="CustomEvent", init={"detail": 2}) is True
    assert host_page.dispatch_event("click", selector="#go", init={"clientX": 7}) is True
    assert host_page.evaluate("__log") == [["document", 2], ["button", 7], ["doc-click", "go", 7, True, True]]


def test_dispatch_event_accepts_a_node_from_query_selector(host_page):
    node = host_page.query_selector("#go")
    assert node is not None
    assert host_page.dispatch_event("click", target=node, init={"clientX": 1}) is True
    assert host_page.evaluate("__log") == [["button", 1], ["doc-click", "go", 1, True, True]]
    assert host_page.query_selector("#missing") is None


def test_dispatch_event_infers_the_class_from_the_event_type(host_page):
    host_page.evaluate("""
        document.querySelector('#go').onkeydown = e => window.__key =
            [e.key, e instanceof KeyboardEvent, Object.prototype.toString.call(e), e.isTrusted];
    """)
    host_page.dispatch_event("keydown", selector="#go", init={"key": "Enter"})
    assert host_page.evaluate("__key") == ["Enter", True, "[object KeyboardEvent]", True]


def test_dispatch_event_reports_cancellation(host_page):
    host_page.evaluate("document.addEventListener('click', e => e.preventDefault(), true)")
    assert host_page.dispatch_event("click", selector="#go") is False


def test_dispatch_event_validates_its_arguments(host_page):
    with pytest.raises(HostStateError) as missing:
        host_page.dispatch_event("click", selector="#missing")
    assert "No element matches" in str(missing.value)
    with pytest.raises(ValueError):
        host_page.dispatch_event("click", target="body")
    with pytest.raises(TypeError):
        host_page.dispatch_event("click", target=object())
    with pytest.raises(TypeError):
        host_page.dispatch_event("click", event_class="NotAnInterface")


def test_set_visibility_transitions_once_per_change(host_page):
    assert host_page.set_visibility(True) is True
    assert host_page.set_visibility(True) is False
    assert host_page.evaluate("[document.hidden, document.visibilityState]") == [True, "hidden"]
    assert host_page.set_visibility(False) is True
    assert host_page.evaluate("[document.hidden, document.visibilityState]") == [False, "visible"]


def test_set_visibility_dispatches_the_lifecycle_events(host_page):
    host_page.evaluate("""
        window.__vis = [];
        document.addEventListener('visibilitychange', () => __vis.push(['change', document.visibilityState]));
        document.addEventListener('freeze', () => __vis.push('freeze'));
        document.addEventListener('resume', () => __vis.push('resume'));
    """)
    host_page.set_visibility(True)
    host_page.set_visibility(False)
    assert host_page.evaluate("__vis") == [["change", "hidden"], "freeze", ["change", "visible"], "resume"]


def test_dispatch_event_is_callable_from_another_thread(host_page):
    results = []
    thread = threading.Thread(target=lambda: results.append(host_page.dispatch_event("click", selector="#go")))
    thread.start()
    thread.join(30)
    assert not thread.is_alive(), "cross-thread dispatch wedged the realm"
    assert results == [True]
    assert host_page.evaluate("__log") == [["button", 0], ["doc-click", "go", 0, True, True]]


def test_dispatch_event_after_close_raises(browser):
    page = browser.new_context().new_page()
    page.set_content("<div id='a'></div>", url="https://example.test/")
    page.close()
    with pytest.raises(Exception):  # executor/invalid-state variants
        page.dispatch_event("click", selector="#a")
