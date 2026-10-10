"""The document's timeline: Chrome starts it at the navigation, not at the process."""
import time

from pyv8c import Browser


def test_the_timeline_restarts_with_each_document():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body>first</body>", url="https://repro.test/first")
        page.run_until_idle(timeout=2)
        assert page.evaluate("Math.round(performance.now())") < 1000
        time.sleep(0.5)
        assert page.evaluate("Math.round(performance.now())") >= 500

        page.set_content("<body>second</body>", url="https://repro.test/second")
        page.run_until_idle(timeout=2)
        # The process has been up longer, so a small reading is the document's own clock.
        assert page.evaluate("Math.round(performance.now())") < 1000
    finally:
        browser.close()


def test_the_origin_is_an_epoch_instant_and_navigation_start_agrees():
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content("<body>body</body>", url="https://repro.test/index.html")
        page.run_until_idle(timeout=2)
        assert page.evaluate(
            "Math.abs(performance.timeOrigin + performance.now() - Date.now()) < 50")
        assert page.evaluate(
            "Math.abs(performance.timing.navigationStart - performance.timeOrigin) < 50")
    finally:
        browser.close()
