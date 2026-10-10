"""Frames whose document the element itself carries: ``srcdoc``, ``data:`` and ``blob:``.

Each one *is* its payload, so none of them is fetched from the network: Chromium decodes all
three in place.  A frame that had to be fetched instead would never finish its load, which is
how a ``data:`` frame used to leave the page waiting (measured against Chrome, see
``examples/5s/README.md``).

The ``data:`` and ``blob:`` frames report from a script in their own document.  The ``srcdoc``
one carries markup only and is read through ``contentDocument``; a ``srcdoc`` document *with* a
script is covered by ``test_a_script_inserted_frame_runs_its_own_document``, which is the case
that used to wedge both pages.
"""
import json
import threading
import time

import pytest

from pyv8c import Browser


def _bounded(action, seconds=10.0):
    """Close on a daemon thread: a frame that never finishes must not wedge the suite."""
    done = threading.Event()

    def run():
        try:
            action()
        except BaseException:  # noqa: BLE001
            pass
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    done.wait(seconds)


def _pump(page, seconds=0.4):
    """Let the queue turn: ``run_until_idle`` raises while a long task runs, so ask for none."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001 - a JS error on the way is not this test's
            pass
        time.sleep(0.01)


def _reporting(label):
    """A frame document that tells the parent it ran, and is named after its body."""
    return ("<!doctype html><body>" + label + "<script>"
            "parent.postMessage('" + label + ":' + document.body.textContent, '*')"
            "</script></body>")


def _literal(label):
    """The frame document as a JavaScript string that never spells ``</script>``.

    ``\/`` is a valid JSON escape that reads the same to JavaScript, so the page's own script
    tag is not closed early by the document it carries.
    """
    return json.dumps(_reporting(label)).replace("</", "<\\/")


#: The frames that report for themselves, and how each one is given its document.
REPORTING = {
    "data": "f.src = 'data:text/html;charset=utf-8,' + encodeURIComponent(INNER);",
    "base64": "f.src = 'data:text/html;charset=utf-8;base64,' + btoa(INNER);",
    "blob": "f.src = URL.createObjectURL(new Blob([INNER], {type: 'text/html'}));",
}

#: The markup a ``srcdoc`` frame carries: no script, so its load finishes.
SRCDOC = "<!doctype html><body>srcdoc<p>plain</p></body>"

PAGE_HTML = """<!doctype html><body><script>
var out = {};
window.__out = out;
window.addEventListener('message', function (event) {
    var parts = String(event.data).split(':');
    out[parts[0]] = parts.slice(1).join(':');
});
function frame(label, setup) {
    var element = document.createElement('iframe');
    element.onload = function () { out[label + '-load'] = true; };
    setup(element);
    document.body.appendChild(element);
}
var INNER = __SRCDOC__;
frame('srcdoc', function (f) { f.srcdoc = INNER; });
__FRAMES__
</script></body>""".replace("__SRCDOC__", json.dumps(SRCDOC)) \
               .replace("__FRAMES__", "\n".join(
                   "INNER = " + _literal(label) + ";\nframe('" + label
                   + "', function (f) { " + setup + " });"
                   for label, setup in REPORTING.items()))


#: A frame a *script* inserts while its own turn is open, whose document carries a script too.
#: Committing that document inline entered the frame's context for its own script while the
#: inserting turn was still on the isolate, and the two waited for each other (measured: the
#: stack stopped inside ``load_iframe_markup``).
SCRIPT_INSERTED_PAGE = """<!doctype html><body><script>
var out = {};
window.__out = out;
window.addEventListener('message', function (event) { out.ran = String(event.data); });
var frame = document.createElement('iframe');
frame.onload = function () { out.load = true; };
frame.srcdoc = __INNER__;
document.body.appendChild(frame);
</script></body>""".replace("__INNER__", _literal("inner"))


@pytest.fixture
def frame_page():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        page.set_content(PAGE_HTML, url="https://frames.test/")
        _pump(page)
        yield page
    finally:
        _bounded(browser.close)


def test_a_script_inserted_frame_runs_its_own_document():
    """The insertion happens inside a script's turn, so the frame commits on a task."""
    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content(SCRIPT_INSERTED_PAGE, url="https://frames.test/")
        _pump(page)
        seen = json.loads(page.evaluate("JSON.stringify(window.__out)"))
        assert seen.get("ran") == "inner:inner", seen
        assert seen.get("load") is True, seen
    finally:
        _bounded(browser.close)


@pytest.mark.parametrize("label", sorted(REPORTING))
def test_a_frame_runs_the_document_it_carries(frame_page, label):
    seen = json.loads(frame_page.evaluate("JSON.stringify(window.__out)"))
    assert seen.get(label) == label, seen
    assert seen.get(label + "-load") is True, seen


def test_the_whole_page_finished_loading(frame_page):
    """Every frame's load fired: a frame that never finishes would leave this page waiting."""
    seen = json.loads(frame_page.evaluate("JSON.stringify(window.__out)"))
    loaded = [key[: -len("-load")] for key in seen if key.endswith("-load")]
    assert sorted(loaded) == sorted([*REPORTING, "srcdoc"]), seen


def test_a_srcdoc_frame_loads_the_markup_it_carries(frame_page):
    assert frame_page.evaluate("""(() => {
        const frame = document.querySelector('iframe');
        return frame.contentDocument.body.textContent;
    })()""") == "srcdocplain"
