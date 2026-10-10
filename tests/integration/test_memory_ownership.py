import gc
import weakref

import pytest

pyv8 = pytest.importorskip('pyv8')
from pyv8c import Browser


def collect():
    for _ in range(3):
        pyv8.low_memory_notification()
        gc.collect()


def test_closed_context_is_removed_from_browser():
    browser = Browser(stub_logging='off')
    refs = []
    try:
        for _ in range(20):
            context = browser.new_context()
            refs.append(weakref.ref(context))
            context.close()
            del context
        collect()
        assert browser.contexts == []
        assert not any(ref() is not None for ref in refs)
    finally:
        browser.close()


def test_closed_pages_are_collectable_with_chrome_installed():
    browser = Browser(stub_logging='off')
    context = browser.new_context()
    refs = []
    try:
        for _ in range(20):
            page = context.new_page()
            assert page.evaluate('typeof chrome.csi') == 'function'
            refs.append(weakref.ref(page))
            page.close()
            del page
        collect()
        assert context.pages == []
        assert not any(ref() is not None for ref in refs)
    finally:
        browser.close()


def test_browser_close_closes_every_context():
    browser = Browser(stub_logging='off')
    contexts = [browser.new_context() for _ in range(4)]
    browser.close()
    assert all(context.closed for context in contexts)
    assert browser.contexts == []


def test_page_cleanup_failure_can_be_retried(monkeypatch):
    browser = Browser(stub_logging='off')
    context = browser.new_context()
    page = context.new_page()
    original = page.net.close
    calls = []

    def fail_once():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('temporary close failure')
        original()

    monkeypatch.setattr(page.net, 'close', fail_once)
    try:
        with pytest.raises(RuntimeError, match='temporary close failure'):
            page.close()
        assert not page.closed
        assert not page.realm.executor.closed
        assert page.tasks.closed
        assert page in context.pages
        page.close()
        assert page.closed
        assert page not in context.pages
    finally:
        browser.close()


def test_browser_cleanup_attempts_other_contexts_and_can_retry(monkeypatch):
    browser = Browser(stub_logging='off')
    first, second = browser.new_context(), browser.new_context()
    original = first.close
    calls = []

    def fail_once():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('temporary close failure')
        original()

    monkeypatch.setattr(first, 'close', fail_once)
    with pytest.raises(RuntimeError, match='temporary close failure'):
        browser.close()
    assert second.closed
    assert browser.contexts == [first]
    browser.close()
    assert first.closed
    assert browser.contexts == []


def test_worker_isolate_failure_preserves_retry_state():
    from types import SimpleNamespace
    from pyv8c.platform.realms import WorkerRealm

    calls = []

    class Isolate:
        def close(self):
            calls.append('isolate')
            if calls.count('isolate') == 1:
                raise RuntimeError('persistent handle')

    session = SimpleNamespace(detach=lambda: calls.append('detach'))
    realm = WorkerRealm(None, SimpleNamespace(session=session))
    realm.context = SimpleNamespace(close=lambda: calls.append('context'))
    realm.isolate = Isolate()
    with pytest.raises(RuntimeError, match='persistent handle'):
        realm.close()
    assert realm.context is None
    assert realm.isolate is not None
    assert calls[:3] == ['context', 'detach', 'isolate']
    realm.close()
    assert realm.isolate is None
