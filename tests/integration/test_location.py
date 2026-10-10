"""Location.ancestorOrigins where there is a frame chain to report.

A page on its own has no ancestors, so this drives the loader: a child frame reads the
list over its parent and a grandchild reads it again, which is what shows the order --
nearest ancestor first, the way Chrome reports it.
"""
import pytest

pytest.importorskip("pyv8")

from pyv8c import Browser

PAGE = "<!doctype html><title>p</title><body></body>"


def _loader(url, request):
    return {"status": 200, "url": str(url), "body": PAGE,
            "headers": {"content-type": "text/html"}}


def _frame(page, origin):
    page.evaluate("""(() => {
        const frame = document.createElement('iframe');
        frame.src = '%s/frame';
        document.body.appendChild(frame);
        return 'asked';
    })()""" % origin)
    page.run_until_idle(timeout=5)
    return next(iter(page._owned.frames))


def test_ancestor_origins_names_the_frames_above_nearest_first():
    browser = Browser(stub_logging="off", resource_loader=_loader)
    top = browser.new_context().new_page()
    top.set_content(PAGE, url="http://top.test/")
    try:
        assert top.evaluate("Array.from(location.ancestorOrigins)") == []
        middle = _frame(top, "http://middle.test")
        assert middle.evaluate("String(location)") == "http://middle.test/frame"
        assert middle.evaluate("Array.from(location.ancestorOrigins)") == ["http://top.test"]
        inner = _frame(middle, "http://inner.test")
        assert inner.evaluate("Array.from(location.ancestorOrigins)") == [
            "http://middle.test", "http://top.test"]
        assert inner.evaluate("location.ancestorOrigins === location.ancestorOrigins") is True
        assert inner.evaluate("location.ancestorOrigins.length") == 2
        # A frame is not its own ancestor, and a bare origin is one of them.
        assert inner.evaluate("location.ancestorOrigins.contains(location.origin)") is False
        assert inner.evaluate("location.ancestorOrigins.contains('http://middle.test')") is True

    finally:
        browser.close()
