"""Every boundary a realm reports through answers the same two questions.

What the error family contains is the page's to observe; a failure of the host's own
is not, at the queued task that raised it and at the one reporter every other shape
funnels into.  The failures below are the shapes pyv8c actually raises: the scheduler's
own ``RealmBusyError``, a bare Python failure, and a class that inherits nothing of the
family -- which is what makes it invisible.
"""
import time

import pytest
from loguru import logger

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c import Browser
from pyv8c.web.page_errors import DOMError, PageTypeError
from pyv8c.foundation.host_errors import RealmBusyError

HOST_FAILURES = (
    RealmBusyError("realm executor did not complete '_cleanup_owner' within 5s"),
    RuntimeError("a bare host failure"),
    DOMError("a host failure that inherits nothing of the family"),
)

HOST_FAILURE_IDS = ["scheduler", "undeclared", "declared-invisible"]


def sink():
    messages = []
    token = logger.add(lambda message: messages.append(str(message).strip()),
                       format="{message}")
    return messages, token


def listening_page():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content("<html><body></body></html>")
    page.evaluate(
        "window.__errors = [];"
        " window.addEventListener('error', (event) => __errors.push(String(event.message)))"
    )
    return browser, page


def drain(page, seconds=0.3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page.run_until_idle(timeout=0)
        time.sleep(0.01)


@pytest.mark.parametrize("failure", HOST_FAILURES, ids=HOST_FAILURE_IDS)
def test_a_task_that_raises_a_host_failure_is_logged_and_not_shown(failure):
    messages, token = sink()
    browser, page = listening_page()
    try:
        page.tasks.post(lambda: (_ for _ in ()).throw(failure))
        drain(page)
        assert page.evaluate("__errors.length") == 0
        assert page.evaluate("6 * 7") == 42
    finally:
        logger.remove(token)
        browser.close()
    assert any("the host could not finish" in line for line in messages), messages


def test_a_task_that_raises_a_declared_error_is_the_pages_to_see():
    browser, page = listening_page()
    try:
        page.tasks.post(lambda: (_ for _ in ()).throw(PageTypeError("a failure the page caused")))
        drain(page)
        assert page.evaluate("__errors.length") == 1
        assert "a failure the page caused" in page.evaluate("__errors[0]")
    finally:
        browser.close()


@pytest.mark.parametrize("failure", HOST_FAILURES, ids=HOST_FAILURE_IDS)
def test_the_reporter_keeps_a_host_failure_off_the_window(failure):
    messages, token = sink()
    browser, page = listening_page()
    try:
        page.report_exception(failure)
        drain(page, 0.1)
        assert page.evaluate("__errors.length") == 0
    finally:
        logger.remove(token)
        browser.close()
    assert any("the host could not finish" in line for line in messages), messages


def test_the_reporter_still_reports_a_declared_error():
    browser, page = listening_page()
    try:
        page.report_exception(PageTypeError("a failure the page caused"))
        drain(page, 0.1)
        assert page.evaluate("__errors.length") == 1
        assert "a failure the page caused" in page.evaluate("__errors[0]")
    finally:
        browser.close()
