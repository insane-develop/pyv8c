"""``document.write``/``writeln`` parse their text at the insertion point."""
import pytest
import pyv8

from pyv8c import Browser


@pytest.fixture
def browser():
    instance = Browser(cache=False, stub_logging="off")
    try:
        yield instance
    finally:
        instance.close()


def test_write_inserts_at_the_insertion_point(browser):
    page = browser.new_context().new_page()
    page.set_content(
        "<html><body><div id='before'></div>"
        "<script>document.write(\"<span id='written'>w</span>\");</script>"
        "<div id='after'></div></body></html>",
        url="https://write.test/",
    )
    assert page.evaluate("""(() => {
        const written = document.getElementById('written');
        return [written.textContent, written.previousElementSibling.getAttribute('id'),
            written.nextElementSibling.id, document.getElementById('after').id,
            document.body.childNodes.length];
    })()""") == ["w", pyv8.Null, "after", "after", 4]


def test_written_scripts_run_like_parser_inserted_scripts(browser):
    page = browser.new_context().new_page()
    page.set_content(
        "<html><body><script>document.write("
        "\"<scr\" + \"ipt>window.order = (window.order || 0) + 1;</scr\" + \"ipt>\""
        ");</script><script>window.order = (window.order || 0) + 10;</script></body></html>",
        url="https://write.test/",
    )
    # The written script is parsed and executed before the following document
    # script, exactly like markup that had been in the document all along.
    assert page.evaluate("[window.order, document.getElementsByTagName('script').length]") == [11, 3]


def test_write_after_load_replaces_the_document(browser):
    page = browser.new_context().new_page()
    page.set_content("<html><body><div id='before'>old</div></body></html>", url="https://write.test/")
    assert page.evaluate("[document.readyState, !!document.getElementById('before')]") == ["complete", True]
    assert page.evaluate("document.write('<i id=after>new</i>')") is pyv8.Undefined
    assert page.evaluate("""(() => ({
        written: document.getElementById('after').tagName,
        before: document.getElementById('before'),
        body: document.body.innerHTML,
        url: document.URL,
    }))()""") == {"written": "I", "before": pyv8.Null, "body": "<i id=\"after\">new</i>",
                   "url": "https://write.test/"}


def test_writeln_appends_a_line_break(browser):
    page = browser.new_context().new_page()
    page.set_content(
        "<html><body><script>"
        "document.writeln('<b id=first>1</b>');document.write('<b id=second>2</b>');"
        "</script></body></html>",
        url="https://write.test/",
    )
    # ``writeln`` ends its text with a newline, so the two written elements are
    # separated by a text node instead of being siblings.
    assert page.evaluate("[document.getElementById('first').nextSibling.nodeValue,"
                         " document.getElementById('second').tagName]") == ["\n", "B"]
