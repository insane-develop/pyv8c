"""Window identity and same-origin policy regression tests (no live network)."""

import pytest

from pyv8c import Browser
from pyv8c.web.bom.window import Window


@pytest.fixture
def windows():
    def loader(url, request):
        body = '<iframe src="http://a.test/nested"></iframe>' if url.endswith('/outer') else '<body>child</body>'
        return {"body": body, "url": url}
    browser = Browser(resource_loader=loader, cache=False)
    page = browser.new_context().new_page()
    page.set_content('<iframe src="http://b.test/child"></iframe>', url='http://a.test/main')
    page.run_until_idle(timeout=1)
    child = next(iter(page._owned.frames))
    try:
        yield page, child
    finally:
        browser.close()


def caught(page, expression):
    return page.evaluate('''(() => {
        const w = document.querySelector('iframe').contentWindow;
        try { ''' + expression + '''; return ['allowed']; }
        catch (e) { return [e.name, e.code, e instanceof DOMException]; }
    })()''')


@pytest.mark.parametrize('expression', [
    'w.document', 'w.navigator', 'w.history', 'w.screen', 'w.localStorage',
    'w.sessionStorage', 'w.custom', 'w.fetch', 'w.setTimeout',
    'w.location.href', 'w.location.origin', 'w.location.host',
    'w.custom = 1', 'w.document = {}', 'w.top = {}', 'delete w.custom',
    'Object.defineProperty(w, "custom", {value: 1})',
    'Object.getOwnPropertyDescriptor(w, "document")',
])
def test_cross_origin_rejects_sensitive_access(windows, expression):
    page, _ = windows
    assert caught(page, expression) == ['SecurityError', 18, True]


def test_cross_origin_safe_members_and_identity(windows):
    page, child = windows
    assert page.evaluate('''(() => { const w = window[0]; return [
        w === document.querySelector('iframe').contentWindow,
        w.window === w, w.self === w, w.frames === w,
        w.top === window, w.parent === window, w.opener === null,
        w.length, w.closed, typeof w.location,
        typeof w.close, typeof w.focus, typeof w.blur, typeof w.postMessage,
        window.top === window, window.parent === window,
        window.self === window, window.window === window,
        document.querySelector('iframe').contentDocument === null
    ]; })()''') == [True] * 7 + [0, False, 'object'] + ['function'] * 4 + [True] * 5
    assert child.evaluate('[top !== window, top === parent, self === window, window.window === window]') == [True] * 4
    assert child.evaluate('try { parent.document; } catch(e) { [e.name, e.code]; }') == ['SecurityError', 18]


def test_same_origin_reads_real_child_and_custom_properties(windows):
    page, child = windows
    child.set_content('<body id="child">hello</body>', url='http://a.test:80/child?x=1#part')
    child.evaluate('window.answer = 42')
    assert page.evaluate('''(() => { const w = window[0]; return [
        w.document.body.id, w.document.URL, w.location.href, w.answer,
        w.document.defaultView === w,
        document.querySelector('iframe').contentDocument === w.document
    ]; })()''') == ['child', 'http://a.test:80/child?x=1#part', 'http://a.test:80/child?x=1#part', 42, True, True]
    page.evaluate('window[0].answer = 73')
    assert child.evaluate('window.answer') == 73
    assert page.evaluate('typeof window[0].doesNotExist') == 'undefined'
    assert child.evaluate('parent.document.URL') == 'http://a.test/main'


def test_nested_frame_top_and_numeric_index_identity(windows):
    page, child = windows
    child.set_content('<iframe src="http://a.test/nested"></iframe>', url='http://a.test/outer')
    child.run_until_idle(timeout=1)
    nested = next(iter(child._owned.frames))
    assert page.evaluate('''[window.length, window[0].length,
        window[0][0].top === window,
        window[0].document.querySelector('iframe').contentWindow === window[0][0],
        window[0][0].parent === window[0]]''') == [1, 1, True, True, True]
    assert nested.evaluate('[top !== window, top !== parent, parent.parent === top]') == [True] * 3
    assert nested.top_page is page


def test_proxy_rechecks_origin_after_navigation(windows):
    page, child = windows
    child.set_content('<body>same</body>', url='http://a.test/same')
    page.evaluate('window.savedFrame = window[0]')
    assert page.evaluate('savedFrame.document.URL') == 'http://a.test/same'
    child.set_content('<body>other</body>', url='http://a.test:8080/other')
    assert page.evaluate('savedFrame === window[0]') is True
    assert page.evaluate('try { savedFrame.document; } catch(e) { [e.name,e.code]; }') == ['SecurityError', 18]


@pytest.mark.parametrize('assignment', ['w.location = "http://b.test/next"', 'w.location.href = "http://b.test/next"'])
def test_cross_origin_location_write_queues_navigation(windows, assignment):
    page, child = windows
    assert caught(page, assignment) == ['allowed']
    child.run_until_idle(timeout=1)
    assert child.url == 'http://b.test/next'
    assert caught(page, 'w.location.href') == ['SecurityError', 18, True]


def test_post_message_delivery_origin_source_and_target_filter(windows):
    page, child = windows
    child.evaluate('window.messages = []; addEventListener("message", e => messages.push([e.data.value,e.origin,e.source===parent]))')
    page.evaluate('window[0].postMessage({value: 1}, "http://b.test"); window[0].postMessage({value: 2}, "http://wrong.test")')
    child.run_until_idle(timeout=1)
    assert child.evaluate('messages') == [[1, 'http://a.test', True]]


@pytest.mark.parametrize('url,allowed', [
    ('about:blank', True), ('blob:http://a.test/token', True),
    ('http://A.TEST:80/other', True), ('http://a.test:8080/other', False),
    ('https://a.test/other', False), ('data:text/html,hello', False),
])
def test_origin_variants(windows, url, allowed):
    page, child = windows
    child.set_content('<body>hello</body>', url=url)
    expected = ['allowed'] if allowed else ['SecurityError', 18, True]
    assert caught(page, 'w.document') == expected


def test_native_window_access_handler_is_automatically_installed(windows):
    page, child = windows
    page.context.expose(nativeChild=child.context.js_obj(child.window))
    assert page.evaluate('try { nativeChild.document; } catch(e) { [e.name,e.code,e instanceof DOMException]; }') == ['SecurityError', 18, True]
    assert page.evaluate('[nativeChild.closed,nativeChild.top===window,nativeChild.self===window[0]]') == [False, True, True]


def test_context_entries_are_released_on_close():
    before = set(Window._context_origins)
    browser = Browser()
    try:
        page = browser.new_context().new_page()
        assert id(page.context) in Window._context_origins
    finally:
        browser.close()
    assert set(Window._context_origins) == before


def test_iframe_src_navigation_retains_proxy(windows):
    page, child = windows
    page.evaluate('window.savedFrame = window[0]; document.querySelector("iframe").src = "http://a.test/new"')
    page.run_until_idle(timeout=1)
    assert page.evaluate('[savedFrame === window[0], savedFrame.document.URL]') == [True, 'http://a.test/new']
    assert len(page._owned.frames) == 1
    assert child.url == 'http://a.test/new'


def test_initial_about_blank_iframe_does_not_need_network():
    browser = Browser(cache=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<iframe></iframe>', url='http://a.test/main')
        page.run_until_idle(timeout=1)
        assert page.evaluate('[window.length, window[0].document.URL, window[0].parent === window]') == [1, 'about:blank', True]
    finally:
        browser.close()


def test_saved_location_is_restricted_after_origin_changes(windows):
    page, child = windows
    child.set_content('<body/>', url='http://a.test/child')
    page.evaluate('window.savedLocation = window[0].location')
    child.set_content('<body/>', url='http://b.test/secret')
    assert page.evaluate('try { savedLocation.href; } catch(e) { [e.name,e.code]; }') == ['SecurityError', 18]


def test_blob_origin_comes_from_creator_page(windows):
    page, child = windows
    creator = page.context_owner.new_page()
    creator.set_content('<body/>', url='data:text/html,creator')
    blob_url = creator.evaluate('URL.createObjectURL(new Blob(["hello"]))')
    child.set_content('<body/>', url=blob_url)
    assert child.origin == creator.origin
    assert child.origin != page.origin
    assert caught(page, 'w.document') == ['SecurityError', 18, True]


def test_onmessage_receives_cross_origin_message(windows):
    page, child = windows
    child.evaluate('window.onmessage = e => window.received = e.data')
    page.evaluate('window[0].postMessage("received", "*")')
    child.run_until_idle(timeout=1)
    assert child.evaluate('received') == 'received'
