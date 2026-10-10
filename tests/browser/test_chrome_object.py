"""``window.chrome`` mirrors the object Chromium installs on every Window."""

import time

import pyv8
from pyv8.trace import format_trace

from pyv8c import Browser


def _page(browser, url="https://example.test/"):
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url=url)
    return page


def _messages(events):
    return [message for message in (format_trace(*event) for event in events) if message is not None]


def test_chrome_object_is_a_plain_global_object():
    browser = Browser(stub_logging="off")
    try:
        page = _page(browser)
        assert page.evaluate("""(() => {
            const chrome = window.chrome;
            return [typeof chrome, 'chrome' in window, chrome === window.chrome,
                Object.getPrototypeOf(chrome) === Object.prototype,
                chrome.constructor === Object,
                Object.prototype.toString.call(chrome),
                Object.keys(chrome), Object.keys(window).includes('chrome'),
                typeof chrome.loadTimes, typeof chrome.csi, typeof chrome.app,
                chrome.runtime === undefined,
                window.__pyv8cChromeHost__ === undefined];
        })()""") == [
            "object", True, True, True, True, "[object Object]",
            ["loadTimes", "csi", "app"], True,
            "function", "function", "object", True, True,
        ]
    finally:
        browser.close()


def test_chrome_global_property_keeps_chromium_attributes():
    browser = Browser(stub_logging="off")
    try:
        page = _page(browser)
        assert page.evaluate("""(() => {
            const descriptor = Object.getOwnPropertyDescriptor(window, 'chrome');
            return [descriptor.writable, descriptor.enumerable, descriptor.configurable,
                'get' in descriptor, 'set' in descriptor];
        })()""") == [True, True, False, False, False]
        # Writable without being configurable: assignment replaces the value,
        # deletion does not remove the slot.
        assert page.evaluate("""(() => {
            window.chrome = 1;
            return [typeof window.chrome, delete window.chrome, typeof window.chrome];
        })()""") == ["number", False, "number"]
    finally:
        browser.close()


def test_chrome_members_are_native_functions():
    browser = Browser(stub_logging="off")
    try:
        page = _page(browser)
        assert page.evaluate("""[
            String(chrome.csi), String(chrome.loadTimes), String(chrome.app.getDetails),
            chrome.csi.name, chrome.loadTimes.name,
            /native code/.test(String(chrome.csi)),
        ]""") == [
            "function csi() { [native code] }",
            "function loadTimes() { [native code] }",
            "function getDetails() { [native code] }",
            "csi", "loadTimes", True,
        ]
    finally:
        browser.close()


def test_chrome_timing_functions_report_chromium_shapes():
    browser = Browser(stub_logging="off")
    try:
        page = _page(browser)
        assert page.evaluate("""(() => {
            const csi = chrome.csi();
            return [Object.keys(csi), csi.tran, typeof csi.startE, csi.pageT >= csi.startE,
                chrome.csi() === csi, chrome.csi().startE === csi.startE,
                chrome.csi().pageT === csi.pageT,
                Object.keys(chrome.loadTimes()),
                [chrome.loadTimes().navigationType, chrome.loadTimes().wasFetchedViaSpdy,
                 chrome.loadTimes().npnNegotiatedProtocol, chrome.loadTimes().connectionInfo,
                 chrome.loadTimes().wasAlternateProtocolAvailable,
                 chrome.loadTimes().firstPaintAfterLoadTime]];
        })()""") == [
            ["startE", "onloadT", "pageT", "tran"], 15, "number", True, False, True, False,
            ["requestTime", "startLoadTime", "commitLoadTime", "finishDocumentLoadTime",
             "finishLoadTime", "firstPaintTime", "firstPaintAfterLoadTime", "navigationType",
             "wasFetchedViaSpdy", "wasNpnNegotiated", "npnNegotiatedProtocol",
             "wasAlternateProtocolAvailable", "connectionInfo"],
            ["Other", True, "h2", "h2", False, 0],
        ]
        first = page.evaluate("chrome.csi().startE")
        # A navigation restarts the clock: with a stale anchor the elapsed time
        # would still contain the sleep below, which the windowed value forbids.
        time.sleep(0.2)
        page.set_content("<body>second</body>", url="https://example.test/next")
        assert page.evaluate(f"chrome.csi().startE >= {first!r}") is True
        assert page.evaluate("chrome.csi().pageT - chrome.csi().startE") < 100
    finally:
        browser.close()


def test_chrome_app_surface_and_missing_runtime():
    browser = Browser(stub_logging="off")
    try:
        page = _page(browser)
        assert page.evaluate("""[
            chrome.app.isInstalled, chrome.app.getIsInstalled(), chrome.app.installState(),
            chrome.app.runningState(), chrome.app.getDetails() === null,
            chrome.app.InstallState.DISABLED, chrome.app.InstallState.INSTALLED,
            chrome.app.InstallState.NOT_INSTALLED, chrome.app.RunningState.CANNOT_RUN,
            chrome.app.RunningState.READY_TO_RUN, chrome.app.RunningState.RUNNING,
            Object.keys(chrome.app)]""") == [
            False, False, "not_installed", "cannot_run", True,
            "disabled", "installed", "not_installed",
            "cannot_run", "ready_to_run", "running",
            ["isInstalled", "getDetails", "getIsInstalled", "installState", "runningState",
             "InstallState", "RunningState"],
        ]
        assert page.evaluate("typeof chrome.runtime") == "undefined"
    finally:
        browser.close()


def test_chrome_is_ready_for_inline_head_scripts():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content(
            "<head><script>window.__chromeType = typeof window.chrome;</script></head>",
            url="https://example.test/",
        )
        assert page.evaluate("__chromeType") == "object"
    finally:
        browser.close()


def test_installing_chrome_is_invisible_to_trace():
    """Host installation is not page behaviour, so it reports no event."""
    events = []
    browser = Browser(trace=True, traceable=True,
                      trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        # Building the realm probes interfaces, none of it about chrome.
        assert [event for event in events if event[2:3] == ("chrome",)] == []
        page.set_content("<body></body>", url="https://example.test/")
        assert [event for event in events if event[2:3] == ("chrome",)] == []
        # Tracing is still attached once the page runs its own script.
        page.evaluate("chrome.csi().tran")
        assert [event[2] for event in events if event[2] in ("chrome", "csi")] == ["chrome", "csi"]
    finally:
        browser.close()


def test_chrome_method_calls_are_reported_with_their_results():
    """The members are Python-backed, so a call is traced like any host method.  An object
    result is reported as a bounded preview of its contents."""
    events = []
    browser = Browser(trace=True, traceable=True,
                      trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = _page(browser)
        events.clear()
        assert page.evaluate("chrome.app.getDetails()") is pyv8.Null
        messages = _messages(events)
        assert messages[0].startswith("[TOP] getter: Window.chrome -> ")
        assert messages[1] == "[TOP] method: ChromeBindings.getDetails() -> null"
        events.clear()
        page.evaluate("chrome.loadTimes().navigationType")
        assert _messages(events)[-1].startswith(
            "[TOP] method: ChromeBindings.loadTimes() -> ")
    finally:
        browser.close()


def test_worker_globals_have_no_chrome_object():
    browser = Browser(stub_logging="off")
    try:
        page = _page(browser)
        page.evaluate("""(() => {
            const source = new Blob(["postMessage(typeof chrome)"], {type: 'text/javascript'});
            const worker = new Worker(URL.createObjectURL(source));
            worker.onmessage = event => window.__workerChrome = event.data;
        })()""")
        page.run_until_idle(timeout=5)
        assert page.evaluate("__workerChrome") == "undefined"
    finally:
        browser.close()
