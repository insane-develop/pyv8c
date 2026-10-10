"""A page reading a resource out of an installed extension.

Chrome answers an installed extension's web-accessible resources locally -- nothing
leaves the browser -- and a page whose origin the extension's ``matches`` names reads
one the way it reads a same-origin response.  Every other probe fails: an unknown id, a
path the extension does not expose, and an extension that is not running.  The shapes
here are what Chrome 154 answers, measured against a profile with one enabled extension
and one suspended one.
"""
import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c import Browser  # noqa: E402

#: Installed and answering; suspended, so it answers a page with nothing; and absent.
INSTALLED = "cneomjecgakdfoeehmmmoiklncdiodmh"
SUSPENDED = "aapbdbdomjkkjkaonfhkkikfgjllcleb"
UNKNOWN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

ICON = "icons/icon_64.png"
EXTENSIONS = {
    INSTALLED: [{"resources": [f"/{ICON}", "/_locales/*/messages.json"],
                 "matches": ["http://*/*", "https://*/*"]}],
}
PAGE = "https://www.radissonhotels.com/zh-cn/"


def url(identifier, path):
    return f"chrome-extension://{identifier}/{path}"


def open_page(extensions=EXTENSIONS, **overrides):
    browser = Browser(stub_logging="off", environment={"extensions": extensions}, **overrides)
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url=PAGE)
    return browser, page


def rows(page, script):
    page.evaluate("window.__rows = [];" + script)
    page.run_until_idle(2)
    return page.evaluate("window.__rows")


def test_an_installed_extension_answers_a_page_the_way_chrome_does():
    browser, page = open_page()
    try:
        collected = rows(page, """
          const push = (...row) => window.__rows.push(row);
          fetch(%(icon)s).then(
            async (response) => push('file', response.status, response.type,
                                     (await response.arrayBuffer()).byteLength > 0),
            (error) => push('file-error', error.name, error.message));
          fetch(%(locale)s).then(
            (response) => push('glob', response.status, response.type),
            (error) => push('glob-error', error.name));
        """ % {"icon": repr(url(INSTALLED, ICON)),
               "locale": repr(url(INSTALLED, "_locales/am/messages.json"))})
    finally:
        browser.close()
    assert collected == [["file", 200, "basic", True], ["glob", 200, "basic"]]


def test_a_probe_that_should_fail_fails_for_every_reason_and_none_is_asked_of_the_engine():
    """A missing id, an undeclared path and an extension that is not running read alike."""
    asked = []

    class Transport:
        def request_sync(self, request):
            asked.append(request.url)
            raise AssertionError("the engine was asked for an extension's resource")

        def request_async(self, tasks, request, callback):
            asked.append(request.url)
            raise AssertionError("the engine was asked for an extension's resource")

    browser, page = open_page(http_engine=Transport)
    try:
        collected = rows(page, """
          const push = (...row) => window.__rows.push(row);
          fetch(%(path)s).catch((error) => push('undeclared-path', error.name, error.message));
          fetch(%(id)s).catch((error) => push('unknown-id', error.name, error.message));
          fetch(%(suspended)s).catch((error) => push('suspended', error.name, error.message));
        """ % {"path": repr(url(INSTALLED, "content.css")),
               "id": repr(url(UNKNOWN, ICON)),
               "suspended": repr(url(SUSPENDED, "popup_css_compiled.css"))})
    finally:
        browser.close()
    assert collected == [["undeclared-path", "TypeError", "Failed to fetch"],
                         ["unknown-id", "TypeError", "Failed to fetch"],
                         ["suspended", "TypeError", "Failed to fetch"]]
    assert asked == []


def test_an_image_of_the_resource_loads_and_reports_its_size():
    browser, page = open_page()
    try:
        collected = rows(page, """
          const push = (...row) => window.__rows.push(row);
          const image = new Image();
          image.onload = () => push('image', image.complete, image.naturalWidth > 0);
          image.onerror = () => push('image', 'error');
          image.src = %(icon)s;
          const blocked = new Image();
          blocked.onload = () => push('undeclared', 'loaded');
          blocked.onerror = () => push('undeclared', 'error');
          blocked.src = %(path)s;
        """ % {"icon": repr(url(INSTALLED, ICON)), "path": repr(url(INSTALLED, "content.css"))})
    finally:
        browser.close()
    assert collected == [["image", True, True], ["undeclared", "error"]]


def test_a_page_the_declaration_does_not_name_cannot_read_the_resource():
    narrow = {INSTALLED: [{"resources": [ICON], "matches": ["https://elsewhere.test/*"]}]}
    browser, page = open_page(narrow)
    try:
        collected = rows(page, """
          fetch(%s).catch((error) => window.__rows.push(error.name));
        """ % repr(url(INSTALLED, ICON)))
    finally:
        browser.close()
    assert collected == ["TypeError"]
