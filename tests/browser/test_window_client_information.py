import pytest

pyv8 = pytest.importorskip("pyv8")
from pyv8c import Browser


def test_client_information_aliases_navigator():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        assert page.evaluate("[window.clientInformation === window.navigator, clientInformation === navigator, clientInformation.userAgent === navigator.userAgent]") == [True, True, True]
    finally:
        browser.close()


def test_client_hints_reach_a_worker():
    """A worker global gets the same hints, through the feature that owns them."""
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://secure.test/")
        page.evaluate("""(() => {
            window.__worker = 'pending';
            const source = new Blob([
                "postMessage(JSON.stringify([typeof navigator.userAgentData," +
                "navigator.userAgentData.brands.map(brand => brand.brand).join(',')," +
                "typeof NavigatorUAData, Object.isFrozen(navigator.userAgentData.brands)]));"
            ], {type: 'text/javascript'});
            const worker = new Worker(URL.createObjectURL(source));
            worker.onmessage = event => window.__worker = event.data;
            worker.onerror = event => window.__worker = 'error:' + event.message;
        })()""")
        page.run_until_idle(timeout=2)
        assert page.evaluate("window.__worker") == \
            '["object","Chromium,Google Chrome,Not A(Brand","function",true]'
    finally:
        browser.close()


