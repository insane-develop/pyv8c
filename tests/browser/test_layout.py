"""Layout and computed-style behavior needed by browser scripts."""

import os

import pytest

from pyv8c import Browser
from pyv8c.web.dom import layout


@pytest.fixture
def page():
    browser = Browser(cache=False, stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://layout.test/")
    try:
        yield page
    finally:
        browser.close()


def test_a_font_lookup_reads_each_root_once(tmp_path, monkeypatch):
    """A page measures text far more often than fonts change: the directory is read
    once, and the repeated lookup answers exactly what the first one did."""
    root = tmp_path / "Fonts"
    root.mkdir()
    for name in ("Arial.ttf", "Arial-Bold.ttf", "Unrelated.otf"):
        (root / name).write_bytes(b"")

    reads, exists = [], []
    listed, present = os.listdir, os.path.exists
    monkeypatch.setattr(os, "listdir", lambda path: (reads.append(str(path)), listed(path))[1])
    monkeypatch.setattr(os.path, "exists",
                        lambda path: (exists.append(str(path)), present(path))[1])
    monkeypatch.setattr(layout, "_FONT_ROOTS", (str(root),))
    layout._FONT_FILES.clear()
    layout._FONT_PATHS.clear()
    try:
        first = layout._font_candidates(["Arial"])
        after_first = len(exists)
        for _ in range(5):
            assert layout._font_candidates(["Arial"]) == first
    finally:
        layout._FONT_FILES.clear()
        layout._FONT_PATHS.clear()

    # The family's files, then the fallback that also exists here: a case-insensitive
    # filesystem reaches the same file through both spellings, so only names are compared.
    assert sorted({os.path.basename(path).lower() for path in first}) == [
        "arial-bold.ttf", "arial.ttf"]
    assert reads == [str(root)]
    assert len(exists) == after_first


def test_hidden_element_keeps_layout_and_font_size_changes_height(page):
    # Height is the pinned text metric for one 114px ``DFPhelvetica`` line.
    assert page.evaluate("""(() => {
        const div = document.createElement('div');
        div.style.visibility = 'hidden';
        document.body.appendChild(div);
        div.innerHTML = '<span style="font-family:mmllii;font-size:114px">mmmmmmmmmmmlliii</span>';
        div.style.fontFamily = 'DFPhelvetica';
        return [div.offsetWidth === document.body.clientWidth, div.offsetHeight,
            getComputedStyle(div).fontFamily,
            getComputedStyle(div.firstElementChild).fontSize,
            div.getBoundingClientRect().height > 0];
    })()""") == [True, 137, "DFPhelvetica", "114px", True]


def test_style_rules_and_basic_flex_grid_geometry(page):
    assert page.evaluate("""(() => {
        document.body.innerHTML = '<style>.item { height: 10px } span { color: red }</style>'
            + '<div id="flex" style="display:flex"><div class="item"></div><div class="item"></div></div>'
            + '<span>text</span>';
        const flex = document.querySelector('#flex');
        const span = document.querySelector('span');
        return [flex.offsetHeight, getComputedStyle(span).color,
            span.offsetHeight, flex.clientWidth === flex.offsetWidth];
    })()""") == [10, "red", 19, True]
