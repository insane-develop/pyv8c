"""Actual browser trace events, rather than Context constructor arguments."""

import pytest
import pyv8
from loguru import logger
from pyv8.trace import format_trace

from pyv8c import Browser


def test_browser_events_lazy_interfaces_and_page_override():
    events = []
    browser = Browser(trace=True, trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        events.clear()
        assert page.evaluate("""(() => {
            const element = document.createElement('div');
            element.id = 'traced';
            const lazy = new AbortController();
            lazy.extra = 42;
            lazy.abort();
            return [element.id, lazy.extra, new Event('ready').type];
        })()""") == ['traced', 42, 'ready']
        assert any(event[0] == 'method' and event[2] == 'createElement' for event in events)
        assert any(event[0] == 'set' and event[2] == 'id' and event[4] == 'traced' for event in events)
        assert any(event[0] == 'get' and event[2] == 'id' and event[4] == 'traced' for event in events)
        assert any(event[0] == 'construct' and event[2] == 'Event' for event in events)
        assert any(event[0] == 'method' and event[2] == 'abort' for event in events)
        # ``extra`` is a plain JS expando on a host object: the engine resolves it
        # outside the binding boundary and the trace reports the stored value.
        assert any(event[0] == 'set' and event[2] == 'extra' and event[4] == 42 for event in events)
        for name in page.binding_session.materialized_interfaces:
            cls = page.binding_session.class_for(name)
            assert not any(key.endswith('_hook__') for base in cls.__mro__ for key in base.__dict__)
        quiet = browser.new_context().new_page(trace=False)
        events.clear()
        quiet.evaluate("document.createElement('div').id")
        assert events == []
    finally:
        browser.close()


def test_default_browser_sink_really_logs():
    messages = []
    sink = logger.add(lambda message: messages.append(message.record['message']))
    browser = Browser(trace=True, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        messages.clear()
        page.evaluate("document.createElement('div').id")
        assert any('method: HTMLDocument.createElement' in message for message in messages)
        assert any('getter: HTMLDivElement.id' in message for message in messages)
    finally:
        browser.close()
        logger.remove(sink)


def _messages(events):
    return [message for message in (format_trace(*event) for event in events) if message is not None]


def test_an_interface_level_member_is_reported_with_its_interface_name():
    """A static member is read off the interface itself, so that is what the trace names."""
    events = []
    browser = Browser(trace=True, traceable=True,
                      trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        page.evaluate("Notification.permission")
        messages = _messages(events)
        assert "[TOP] getter: Notification.permission -> 'default'" in messages
        assert not any(message.startswith("[TOP] getter: NoneType.") for message in messages)
    finally:
        browser.close()


def test_an_object_read_stays_on_one_bounded_line():
    """A converted object carried whole graphs, and its embedded newlines broke the
    one-event-per-line shape of the trace; it is a preview now, and a conversion that
    raises still reports the event instead of losing it."""
    events = []
    browser = Browser(trace=True, traceable=True, stub_logging="off",
                      trace_callback=lambda *event: events.append(event))
    try:
        page = browser.new_context().new_page()
        page.set_content("<body><p>x</p></body>", url="https://example.test/")
        page.evaluate("""(() => {
            const cyclic = { name: 'cyclic' };
            cyclic.self = cyclic;
            window.cyclic = cyclic;
            window.big = new Array(400).fill('line\\nline\\nline');
            return [window.cyclic, window.big].length;
        })()""")
        messages = _messages(events)
    finally:
        browser.close()

    assert any("Window.cyclic" in message for message in messages)
    assert any("Window.big" in message for message in messages)
    assert not any("\n" in message for message in messages)
    assert any(message.endswith("...") for message in messages)


def test_declined_reads_are_reported_with_the_value_they_resolve_to():
    """Native globals resolve outside the binding and still carry their value."""
    events = []
    browser = Browser(trace=True, traceable=True, trace_callback=lambda *event: events.append(event),
                      stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        events.clear()
        assert page.evaluate("eval") is not None
        assert _messages(events) == ["[TOP] getter: Window.eval -> function eval() { [native code] }"]
        events.clear()
        # A name no property provides has no value to show, so it stays silent.
        assert page.evaluate("window.notThere") is pyv8.Undefined
        assert _messages(events) == []
    finally:
        browser.close()


def test_probes_are_reported_separately_from_reads():
    """Existence and descriptor probes get their own events, reads stay single."""
    events = []
    browser = Browser(trace=True, traceable=True, trace_callback=lambda *event: events.append(event),
                      stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        events.clear()
        assert page.evaluate("'eval' in window") is True
        assert page.evaluate("'notThere' in window") is False
        assert page.evaluate("Object.getOwnPropertyDescriptor(window, 'eval') === undefined") is False
        probes = [message for message in _messages(events) if " has: " in message or " descriptor: " in message]
        assert probes == [
            "[TOP] has: Window.eval -> True",
            "[TOP] has: Window.notThere -> False",
            "[TOP] descriptor: Window.eval -> True",
        ]
        assert [event for event in events if event[0] == "get" and event[2] == "eval"] == []
        events.clear()
        assert page.evaluate("typeof window.eval") == 'function'
        assert [event for event in events if event[0] in ("has", "descriptor")] == []
        assert [message for message in _messages(events) if "eval" in message] == [
            "[TOP] getter: Window.eval -> function eval() { [native code] }"]
    finally:
        browser.close()


def test_declared_getters_are_reported_once_with_their_value():
    events = []
    browser = Browser(trace=True, traceable=True, trace_callback=lambda *event: events.append(event),
                      stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        events.clear()
        page.evaluate("navigator.userAgent")
        messages = _messages(events)
        assert [message.split(" -> ")[0] for message in messages] == [
            "[TOP] getter: Window.navigator",
            "[TOP] getter: Navigator.userAgent",
        ]
        assert "undefined" not in messages[-1]
    finally:
        browser.close()


def test_a_frames_realm_is_labelled_apart_from_the_pages_own():
    """A frame's realm is not the page's: its events say FRAME, not TOP."""
    events = []
    browser = Browser(trace=True, traceable=True, trace_callback=lambda *event: events.append(event),
                      stub_logging="off")
    try:
        page = browser.new_context().new_page()
        events.clear()
        page.set_content('<body><iframe srcdoc="&lt;script&gt;navigator.userAgent&lt;/script&gt;">'
                         "</iframe></body>", url="https://example.test/")
        messages = _messages(events)
        assert any(message.startswith("[FRAME] ") and "userAgent" in message for message in messages)
        assert not any(message.startswith("[TOP] ") and "userAgent" in message for message in messages)
    finally:
        browser.close()


def test_worker_inherits_context_sink_without_faker_switch():
    events = []
    browser = Browser(trace=True, trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.evaluate("""(() => {
            const source = new Blob(["postMessage(new Event('worker-trace').type)"], {type:'text/javascript'});
            const worker = new Worker(URL.createObjectURL(source));
            worker.onmessage = event => window.workerResult = event.data;
        })()""")
        page.run_until_idle(timeout=5)
        assert page.evaluate("workerResult") == 'worker-trace'
        assert any(event[7] == 'Worker' and event[0] == 'construct' and event[2] == 'Event' for event in events)
    finally:
        browser.close()


def test_trace_keeps_cross_origin_access_denied():
    browser = Browser(trace=True, trace_callback=lambda *event: None, stub_logging="off",
                      resource_loader=lambda url, request: {'body': '<body>child</body>', 'url': url}, cache=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<iframe src="http://b.test/child"></iframe>', url='http://a.test/main')
        page.run_until_idle(timeout=2)
        assert page.evaluate("try { window[0].document; } catch(error) { error.name; }") == 'SecurityError'
    finally:
        browser.close()


@pytest.mark.parametrize("initial", [False, True])
def test_page_trace_can_pause_and_resume(initial):
    events = []
    browser = Browser(trace=initial, traceable=True,
                      trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        quiet = browser.new_context().new_page(trace=False)
        page.evaluate("window.element = document.createElement('div')")
        original_context = page.context
        for enabled in (True, False, True, False):
            page.trace = enabled
            events.clear()
            assert page.evaluate("element.id='value';element.id") == 'value'
            assert bool(events) is enabled
            assert page.trace is enabled
            assert page.context.trace is enabled
            assert page.services.trace is enabled
            assert page.context is original_context
            events.clear()
            quiet.evaluate("document.createElement('div').id")
            assert events == []
        with pytest.raises(TypeError, match='bool'):
            page.trace = 1
        page.close()
        with pytest.raises(RuntimeError):
            page.trace = True
    finally:
        browser.close()


def test_page_without_instrumentation_rejects_activation():
    browser = Browser(stub_logging='off')
    try:
        page = browser.new_context().new_page()
        with pytest.raises(RuntimeError, match='traceable=True'):
            page.trace = True
        assert page.trace is False
        assert page.services.trace is False
    finally:
        browser.close()


def test_invalid_browser_sink():
    with pytest.raises(TypeError, match='trace_callback'):
        Browser(trace_callback=42)
