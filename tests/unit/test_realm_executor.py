import threading
import time

import pytest
from loguru import logger

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")
pytestmark = pytest.mark.native

from pyv8c import Browser
from pyv8c.foundation.host_errors import RealmBusyError
from pyv8c.foundation.executor import RealmExecutor


def _late_failure():
    time.sleep(0.3)
    raise RuntimeError("late failure")


def _late_success():
    time.sleep(0.2)
    return "late"


def sink(messages):
    """Capture log lines, so a warning about a command that outlived its caller is readable."""
    return logger.add(lambda message: messages.append(str(message).strip()), format="{message}")


def wait_for_lines(messages, count=1, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(messages) < count:
        time.sleep(0.02)


def test_executor_runs_calls_on_one_owner_thread_and_closes_waiters():
    executor = RealmExecutor(name="test-realm")
    try:
        caller = threading.get_ident()
        owner = executor.call(threading.get_ident)
        assert owner != caller
        assert executor.call(lambda: executor.is_owner_thread()) is True
    finally:
        executor.close()
    with pytest.raises(RuntimeError, match="closed"):
        executor.call(lambda: None)


def test_page_background_timer_runs_without_manual_queue_pump():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        page.evaluate("window.__executorTimer = 0; setTimeout(() => __executorTimer = 1, 10)")
        time.sleep(0.2)
        assert page.evaluate("__executorTimer") == 1
    finally:
        browser.close()


def test_page_close_stops_realm_executor():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.close()
    assert page.realm.executor.closed
    browser.close()


def test_timed_out_queued_command_is_not_executed_later():
    executor = RealmExecutor(name="test-cancel")
    entered = threading.Event()
    release = threading.Event()
    side_effect = []
    try:
        executor.post(lambda: (entered.set(), release.wait(2)))
        assert entered.wait(1)
        with pytest.raises(RealmBusyError, match="command cancelled"):
            executor.call(lambda: side_effect.append("ghost"), timeout=0.01)
        release.set()
        time.sleep(0.05)
        assert side_effect == []
    finally:
        release.set()
        executor.close()


def test_a_command_that_outlives_its_caller_reports_what_it_ended_with():
    """The reason a wedged realm finally gives up is logged, not swallowed."""
    messages = []
    token = sink(messages)
    executor = RealmExecutor(name="test-late-failure")
    try:
        with pytest.raises(RealmBusyError, match="its outcome is logged"):
            executor.call(_late_failure, timeout=0.05)
        wait_for_lines(messages)
    finally:
        logger.remove(token)
        executor.close()
    assert len(messages) == 1
    assert "'_late_failure' failed" in messages[0]
    assert "RuntimeError: late failure" in messages[0]
    assert "after it was submitted" in messages[0]
    # The caller's own budget is repeated: ending just past it means the command
    # started near the deadline rather than being slow itself.
    assert "its caller waited 0.05s" in messages[0]


def test_a_command_that_finishes_late_is_reported_too():
    messages = []
    token = sink(messages)
    executor = RealmExecutor(name="test-late-success")
    try:
        with pytest.raises(RealmBusyError):
            executor.call(_late_success, timeout=0.05)
        wait_for_lines(messages)
    finally:
        logger.remove(token)
        executor.close()
    assert len(messages) == 1
    assert "'_late_success' finished" in messages[0]
    assert "failed" not in messages[0]
