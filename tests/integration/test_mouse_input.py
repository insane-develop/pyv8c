"""Host-driven mouse input: ``page.mouse``, ``page.click`` and what a page sees.

Every sequence asserted here is the one Chrome 153 was observed dispatching for
the same scenario; ``tests/repros/test_mouse_events_vs_chrome.py`` re-runs them
side by side and compares the two logs.
"""
import time

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c import Browser
from pyv8c.foundation.host_errors import HostStateError

#: Records the members the mouse events are held to, in the order they fire.
#: Coordinates are left out: the layout model is coarser than Chromium's for
#: inline and positioned boxes, and this file is about the event sequence.
RECORDER = """
globalThis.log = [];
const record = (type) => (e) => log.push([type, e.target.id || e.target.localName,
    String(e.detail), String(e.isTrusted), String(e.button), String(e.buttons)]);
for (const type of ['pointerover', 'pointerout', 'pointerenter', 'pointerleave', 'pointermove',
                    'pointerdown', 'pointerup', 'mouseover', 'mouseout', 'mouseenter',
                    'mouseleave', 'mousemove', 'mousedown', 'mouseup', 'click', 'dblclick',
                    'auxclick', 'contextmenu', 'focus', 'focusin', 'blur', 'focusout',
                    'input', 'change', 'submit'])
  addEventListener(type, record(type), true);
"""

HTML = """
<!DOCTYPE html><html><body style="margin:0">
<div id="a" style="display:block;width:100px;height:40px"></div>
<div id="b" style="display:block;width:100px;height:40px"></div>
<a id="link" href="#frag" style="display:block;width:120px;height:30px">link</a>
<button id="btn" style="display:block;width:120px;height:30px">go</button>
<input id="box" type="checkbox" style="display:block;width:20px;height:20px">
<label id="lab" for="box">label</label>
<input id="text" type="text" style="display:block;width:120px;height:20px">
<form id="form" action="/submitted">
  <button id="submit" type="submit" style="display:block;width:120px;height:30px">send</button>
</form>
<button id="disabled" disabled style="display:block;width:120px;height:30px">no</button>
<div id="outer" style="display:block;width:200px;height:80px">
  <div id="inner" style="display:block;width:80px;height:80px"></div>
</div>
<script>__RECORDER__</script>
</body></html>
"""


@pytest.fixture
def page():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content(HTML.replace("__RECORDER__", RECORDER), url="https://example.test/")
    try:
        yield page
    finally:
        browser.close()


def centre(page, selector) -> tuple[float, float]:
    """The centre of the element's box, computed the way a page would."""
    values = page.evaluate(
        """(() => { const r = document.querySelector('%s').getBoundingClientRect();
             return [r.x + r.width / 2, r.y + r.height / 2]; })()""" % selector
    )
    return float(values[0]), float(values[1])


def events(page):
    """The event names the page recorded, in order."""
    return [row[0] for row in page.evaluate("log")]


def clear_log(page):
    page.evaluate("log.length = 0")


def active_element(page):
    return page.evaluate(
        "document.activeElement && (document.activeElement.id || document.activeElement.localName)"
    )


# -- moving ------------------------------------------------------------------


def test_a_first_move_reports_both_over_events_then_both_move_events(page):
    page.mouse.move(*centre(page, "#a"))
    assert page.evaluate("log") == [
        # A pointer event that reports no button transition carries -1, and the
        # mouse event beside it the 0 Chromium gives that one.
        ["pointerover", "a", "0", "true", "-1", "0"],
        ["mouseover", "a", "0", "true", "0", "0"],
        ["pointermove", "a", "0", "true", "-1", "0"],
        ["mousemove", "a", "0", "true", "0", "0"],
    ]


def hit_tests(page, monkeypatch, action):
    """How many hit tests *action* makes: one walk of the tree per input position."""
    from pyv8c.platform import input as input_module

    asked = []
    original = input_module.element_from_point

    def counted(document, x, y):
        asked.append((x, y))
        return original(document, x, y)

    monkeypatch.setattr(input_module, "element_from_point", counted)
    action()
    return len(asked)


def test_a_step_asks_which_element_is_under_the_cursor_once(page, monkeypatch):
    """The hit test walks the document, so a position resolves its point once."""
    assert hit_tests(page, monkeypatch, lambda: page.mouse.move(50, 20, steps=3)) == 3


def test_a_press_and_a_release_each_read_their_own_position(page, monkeypatch):
    assert hit_tests(page, monkeypatch, lambda: page.mouse.click(50, 20)) == 3


def test_hovering_an_element_reuses_the_hit_the_covered_check_read(page, monkeypatch):
    assert hit_tests(page, monkeypatch, lambda: page.hover("#a")) == 1


def test_clicking_an_element_reuses_it_for_the_move_only(page, monkeypatch):
    assert hit_tests(page, monkeypatch, lambda: page.click("#a")) == 3


def test_the_page_reports_the_cursor_arriving_in_it_once(page):
    """Chrome fires the document's own enter when the pointer arrives, and not again
    while it stays inside; the enter is the outermost one, before the element's."""
    page.evaluate("""globalThis.page_log = [];
        for (const type of ['pointerenter', 'mouseenter', 'pointerover', 'mouseover'])
            document.addEventListener(type, event =>
                page_log.push(type + ':' + (event.target.id || event.target.localName ||
                                            '#document')), true);""")
    assert page.evaluate("page_log") == []
    page.mouse.move(*centre(page, "#a"))
    assert page.evaluate("page_log") == ["pointerover:a", "pointerenter:#document",
                                         "mouseover:a", "mouseenter:#document"]
    page.evaluate("page_log.length = 0")
    page.mouse.move(*centre(page, "#b"))
    assert page.evaluate("page_log") == ["pointerover:b", "mouseover:b"]


def test_crossing_into_another_element_reports_out_then_over(page):
    page.mouse.move(*centre(page, "#a"))
    clear_log(page)
    page.mouse.move(*centre(page, "#b"))
    assert page.evaluate("log") == [
        ["pointerout", "a", "0", "true", "-1", "0"],
        ["pointerover", "b", "0", "true", "-1", "0"],
        ["mouseout", "a", "0", "true", "0", "0"],
        ["mouseover", "b", "0", "true", "0", "0"],
        ["pointermove", "b", "0", "true", "-1", "0"],
        ["mousemove", "b", "0", "true", "0", "0"],
    ]


def test_a_move_inside_one_element_reports_only_the_move_events(page):
    page.mouse.move(*centre(page, "#a"))
    clear_log(page)
    x, y = centre(page, "#a")
    page.mouse.move(x + 1, y + 1)
    assert events(page) == ["pointermove", "mousemove"]


def test_enter_and_leave_reach_the_element_and_not_its_ancestors(page):
    """Chromium dispatches them with a propagation path of just the target, and the
    document's own enter -- the cursor arriving in the page -- is a separate one."""
    page.evaluate("""
        globalThis.enters = [];
        document.addEventListener('mouseenter', () => enters.push('document'), true);
        document.querySelector('#a').addEventListener('mouseenter', () => enters.push('a'));
    """)
    page.mouse.move(*centre(page, "#a"))
    page.mouse.move(*centre(page, "#b"))
    assert page.evaluate("enters") == ["document", "a"]


def test_steps_report_each_intermediate_position(page):
    page.mouse.move(0, 0)
    clear_log(page)
    page.mouse.move(30, 0, steps=3)
    assert page.mouse.position == (30.0, 0.0)
    assert events(page).count("mousemove") == 3


def test_a_move_reports_the_coordinate_pairs_and_the_movement(page):
    """Chrome reports screen and page coordinates beside the viewport pair, whole
    pixels on a mouse event, and the movement the cursor made: none on the first
    move, and none on a press or a click."""
    page.evaluate("""globalThis.cursor = [];
        for (const type of ['pointermove', 'mousemove', 'pointerdown', 'mousedown',
                            'pointerup', 'mouseup', 'click'])
            addEventListener(type, (e) => cursor.push([type, e.clientX, e.clientY,
                e.screenX, e.screenY, e.pageX, e.pageY, e.movementX, e.movementY]), true);""")
    page.mouse.move(30, 40)
    page.mouse.move(70, 60)
    page.mouse.down()
    page.mouse.up()
    page.run_until_idle(timeout=1)

    assert page.evaluate("globalThis.cursor") == [
        ["pointermove", 30, 40, 30, 40, 30, 40, 0, 0],
        ["mousemove", 30, 40, 30, 40, 30, 40, 0, 0],
        ["pointermove", 70, 60, 70, 60, 70, 60, 40, 20],
        ["mousemove", 70, 60, 70, 60, 70, 60, 40, 20],
        ["pointerdown", 70, 60, 70, 60, 70, 60, 0, 0],
        ["mousedown", 70, 60, 70, 60, 70, 60, 0, 0],
        ["pointerup", 70, 60, 70, 60, 70, 60, 0, 0],
        ["mouseup", 70, 60, 70, 60, 70, 60, 0, 0],
        ["click", 70, 60, 70, 60, 70, 60, 0, 0],
    ]


# -- pressing and clicking ---------------------------------------------------


def test_a_click_reports_press_focus_release_and_click(page):
    page.mouse.click(*centre(page, "#btn"))
    assert events(page)[-7:] == [
        "pointerdown", "mousedown", "focus", "focusin", "pointerup", "mouseup", "click",
    ]
    assert [row[1] for row in page.evaluate("log")][-7:] == ["btn"] * 7
    assert active_element(page) == "btn"


def test_the_press_and_the_click_report_detail_one(page):
    page.mouse.click(*centre(page, "#btn"))
    details = {row[0]: row[2] for row in page.evaluate("log")}
    assert details["mousedown"] == "1"
    assert details["mouseup"] == "1"
    assert details["click"] == "1"
    assert details["pointerdown"] == "0"


def test_the_pressed_button_is_in_the_mask_until_it_is_released(page):
    page.mouse.click(*centre(page, "#btn"))
    masks = {row[0]: row[5] for row in page.evaluate("log")}
    assert masks["pointerdown"] == "1"
    assert masks["mousedown"] == "1"
    assert masks["mouseup"] == "0"
    assert masks["click"] == "0"


def test_a_double_click_reports_detail_two_and_ends_in_dblclick(page):
    page.mouse.dblclick(*centre(page, "#btn"))
    assert events(page)[-13:] == [
        "pointerdown", "mousedown", "focus", "focusin", "pointerup", "mouseup", "click",
        "pointerdown", "mousedown", "pointerup", "mouseup", "click", "dblclick",
    ]
    counted = [(row[0], row[2]) for row in page.evaluate("log")
               if row[0] in ("mousedown", "click", "dblclick")]
    assert counted == [("mousedown", "1"), ("click", "1"), ("mousedown", "2"),
                       ("click", "2"), ("dblclick", "2")]


def test_a_press_and_release_on_different_elements_click_their_common_ancestor(page):
    page.mouse.move(*centre(page, "#inner"))
    page.mouse.down()
    x, y = centre(page, "#outer")
    page.mouse.move(x + 60, y)
    page.mouse.up()
    tail = page.evaluate("log")[-1]
    assert tail[0] == "click"
    assert tail[1] == "outer"


def test_a_press_without_a_release_never_clicks(page):
    page.mouse.move(*centre(page, "#btn"))
    clear_log(page)
    page.mouse.down()
    assert events(page) == ["pointerdown", "mousedown", "focus", "focusin"]
    assert page.mouse.pressed == frozenset({"left"})
    page.mouse.up()
    assert page.mouse.pressed == frozenset()


def test_the_right_button_auxclicks_and_opens_a_context_menu(page):
    page.mouse.click(*centre(page, "#btn"), button="right")
    assert events(page)[-4:] == ["pointerup", "mouseup", "auxclick", "contextmenu"]
    buttons = {row[0]: row[4] for row in page.evaluate("log")}
    assert buttons["mousedown"] == "2"
    assert "click" not in buttons


def test_a_cancelled_mousedown_leaves_focus_where_it_was(page):
    page.evaluate("document.getElementById('btn').addEventListener('mousedown',"
                  " e => e.preventDefault())")
    page.evaluate("document.getElementById('text').focus()")
    page.mouse.click(*centre(page, "#btn"))
    assert active_element(page) == "text"
    assert events(page)[-1] == "click"


def test_a_cancelled_mousedown_still_runs_the_click_activation(page):
    """Chrome takes away focus on a cancelled press and nothing else."""
    page.evaluate("document.getElementById('link').addEventListener('mousedown',"
                  " e => e.preventDefault())")
    page.mouse.click(*centre(page, "#link"))
    assert page.url == "https://example.test/#frag"


def test_a_disabled_control_receives_pointer_events_only(page):
    page.mouse.click(*centre(page, "#disabled"))
    assert events(page) == [
        "pointerover", "mouseover", "pointermove", "mousemove", "pointerdown", "pointerup",
    ]
    assert active_element(page) == "body"


def test_clicking_an_element_with_no_box_is_refused(page):
    page.evaluate("document.body.innerHTML = '<div id=\"flat\" style=\"height:0\"></div>'")
    with pytest.raises(HostStateError) as refused:
        page.click("#flat")
    assert "no box to click" in str(refused.value)


def test_moving_to_an_element_that_is_not_rendered_is_refused(page):
    page.evaluate("document.body.innerHTML = '<div id=\"gone\" style=\"display:none\"></div>'")
    with pytest.raises(HostStateError) as refused:
        page.hover("#gone")
    assert "not rendered" in str(refused.value)


def test_an_unknown_button_name_is_refused(page):
    with pytest.raises(ValueError) as refused:
        page.mouse.click(1, 1, button="thumb")
    assert "thumb" in str(refused.value)


# -- what a click activates --------------------------------------------------


def test_clicking_a_link_follows_it(page):
    page.mouse.click(*centre(page, "#link"))
    assert page.url == "https://example.test/#frag"


def test_clicking_a_checkbox_toggles_it_and_fires_input_and_change(page):
    page.mouse.click(*centre(page, "#box"))
    assert page.evaluate("document.getElementById('box').checked") is True
    assert events(page)[-3:] == ["click", "input", "change"]
    assert [row[3] for row in page.evaluate("log")][-3:] == ["true", "true", "true"]


def test_clicking_a_label_reaches_the_control_it_labels(page):
    """A label forwards the press it got: trusted, with its click count."""
    page.mouse.click(*centre(page, "#lab"))
    assert page.evaluate("document.getElementById('box').checked") is True
    assert page.evaluate("log")[-6:] == [
        ["click", "lab", "1", "true", "0", "0"],
        ["focus", "box", "0", "true", "undefined", "undefined"],
        ["focusin", "box", "0", "true", "undefined", "undefined"],
        ["click", "box", "1", "true", "0", "0"],
        ["input", "box", "undefined", "true", "undefined", "undefined"],
        ["change", "box", "undefined", "true", "undefined", "undefined"],
    ]


def test_a_scripted_label_click_forwards_an_untrusted_click_instead(page):
    """``label.click()`` reaches the control as the synthetic click it is."""
    page.evaluate("document.getElementById('lab').click()")
    assert page.evaluate("document.getElementById('box').checked") is True
    assert page.evaluate("log")[-6:] == [
        ["click", "lab", "0", "false", "0", "0"],
        ["focus", "box", "0", "true", "undefined", "undefined"],
        ["focusin", "box", "0", "true", "undefined", "undefined"],
        ["click", "box", "0", "false", "0", "0"],
        ["input", "box", "undefined", "true", "undefined", "undefined"],
        ["change", "box", "undefined", "true", "undefined", "undefined"],
    ]


def test_clicking_a_submit_button_submits_the_form(page):
    page.mouse.click(*centre(page, "#submit"))
    assert [row for row in page.evaluate("log") if row[0] == "submit"] == [
        ["submit", "form", "undefined", "true", "undefined", "undefined"],
    ]


def test_a_cancelled_submit_event_stops_the_submission(page):
    page.evaluate("document.getElementById('form').addEventListener('submit',"
                  " e => e.preventDefault())")
    page.mouse.click(*centre(page, "#submit"))
    assert events(page)[-1] == "submit"
    assert page.url == "https://example.test/"


def test_element_click_uses_the_synthetic_steps(page):
    page.evaluate("document.getElementById('box').click()")
    assert page.evaluate("document.getElementById('box').checked") is True
    assert page.evaluate("log")[-3:] == [
        ["click", "box", "0", "false", "0", "0"],
        ["input", "box", "undefined", "true", "undefined", "undefined"],
        ["change", "box", "undefined", "true", "undefined", "undefined"],
    ]


def test_element_click_on_a_link_navigates(page):
    page.evaluate("document.getElementById('link').click()")
    assert page.url == "https://example.test/#frag"


def test_element_click_on_a_disabled_control_dispatches_nothing(page):
    page.evaluate("document.getElementById('disabled').click()")
    assert page.evaluate("log") == []
    assert active_element(page) == "body"


def test_element_click_on_a_plain_div_only_fires_the_event(page):
    page.evaluate("document.getElementById('a').click()")
    assert page.evaluate("log") == [["click", "a", "0", "false", "0", "0"]]


# -- focus -------------------------------------------------------------------


def test_the_body_is_the_active_element_until_something_takes_focus(page):
    assert active_element(page) == "body"
    assert page.evaluate("document.hasFocus()") is True


def test_a_plain_div_cannot_take_focus(page):
    page.evaluate("document.getElementById('a').focus()")
    assert active_element(page) == "body"


def test_a_click_focuses_the_nearest_focusable_ancestor(page):
    page.evaluate("document.getElementById('btn').innerHTML = '<span>x</span>'")
    page.mouse.click(*centre(page, "#btn"))
    assert active_element(page) == "btn"


def test_clicking_a_plain_element_clears_focus(page):
    page.evaluate("document.getElementById('text').focus()")
    page.mouse.click(*centre(page, "#a"))
    assert active_element(page) == "body"
    assert [name for name in events(page) if name in ("blur", "focusout")] == ["blur", "focusout"]


def test_tab_index_reports_the_default_of_the_element(page):
    assert page.evaluate("""(document.getElementById('btn').tabIndex + ' '
        + document.getElementById('a').tabIndex + ' '
        + document.getElementById('text').tabIndex)""") == "0 -1 0"


def test_tab_index_reflects_the_attribute_and_makes_an_element_focusable(page):
    page.evaluate("document.getElementById('a').setAttribute('tabindex', '3')")
    assert page.evaluate("document.getElementById('a').tabIndex") == 3
    page.evaluate("document.getElementById('a').tabIndex = 1")
    assert page.evaluate("document.getElementById('a').getAttribute('tabindex')") == "1"
    assert page.evaluate("(document.getElementById('a').focus(), document.activeElement.id)") == "a"


# -- hit testing -------------------------------------------------------------


def test_element_from_point_returns_the_topmost_element(page):
    x, y = centre(page, "#a")
    assert page.evaluate("document.elementFromPoint(%g, %g).id" % (x, y)) == "a"


def test_elements_from_point_returns_the_stack_deepest_first(page):
    x, y = centre(page, "#inner")
    assert page.evaluate(
        "document.elementsFromPoint(%g, %g).map(e => e.id || e.localName)" % (x, y)
    ) == ["inner", "outer", "body", "html"]


def test_a_point_outside_the_viewport_hits_nothing(page):
    assert page.evaluate("String(document.elementFromPoint(-5, -5))") == "null"


def test_display_none_is_transparent_to_a_hit_test(page):
    page.evaluate("""(() => {
        const gone = document.createElement('div');
        gone.id = 'gone';
        gone.style.cssText = 'display:none;width:50px;height:50px';
        document.body.insertBefore(gone, document.body.firstChild);
    })()""")
    x, y = centre(page, "#a")
    assert page.evaluate("document.elementFromPoint(%g, %g).id" % (x, y)) == "a"


def test_visibility_and_pointer_events_can_be_turned_back_on_by_a_child(page):
    page.evaluate("""(() => {
        document.body.innerHTML =
            '<div id="pen" style="pointer-events:none;width:100px;height:50px">'
            + '<div id="penchild" style="pointer-events:auto;width:100px;height:50px"></div></div>'
            + '<div id="vish" style="visibility:hidden;width:100px;height:50px">'
            + '<div id="vischild" style="visibility:visible;width:100px;height:50px"></div></div>';
    })()""")
    assert page.evaluate("document.elementFromPoint(50, 25).id") == "penchild"
    assert page.evaluate("document.elementFromPoint(50, 75).id") == "vischild"


# -- the selector API --------------------------------------------------------


def test_page_click_moves_to_the_element_and_clicks_it(page):
    page.click("#btn")
    assert active_element(page) == "btn"
    assert events(page)[-1] == "click"
    assert page.mouse.position == centre(page, "#btn")


def test_page_click_refuses_a_selector_that_matches_nothing(page):
    with pytest.raises(HostStateError) as refused:
        page.click("#missing")
    assert "No element matches" in str(refused.value)


def test_page_hover_leaves_the_element_hovered(page):
    page.hover("#btn")
    assert events(page) == ["pointerover", "mouseover", "pointermove", "mousemove"]
    assert page.mouse.position == centre(page, "#btn")


def test_page_dblclick_counts_both_clicks(page):
    page.dblclick("#btn")
    assert events(page)[-1] == "dblclick"
    assert [(row[0], row[2]) for row in page.evaluate("log") if row[0] == "click"] == [
        ("click", "1"), ("click", "2"),
    ]


# -- what an event's timeStamp reads -------------------------------------------


def test_a_dispatched_event_is_stamped_on_the_documents_clock(page):
    """Chrome reports the reading a page can compare with performance.now() directly."""
    page.evaluate("""(() => { globalThis.stamps = [];
      addEventListener('mousemove', (e) => stamps.push([e.timeStamp, performance.now()])); })()""")
    page.mouse.move(50, 20)
    stamp, now = page.evaluate("stamps[0]")
    assert 0 <= stamp <= now + 1


def test_a_page_made_event_keeps_the_moment_it_was_made(page):
    """The stamp belongs to the moment of construction: reading it later still reports then."""
    page.evaluate("globalThis.made = new Event('x')")
    # The document's clock follows real time, so a host pause is the wait that shows it.
    page.run_until_idle(timeout=0)
    time.sleep(0.05)
    behind = page.evaluate("globalThis.made.timeStamp - performance.now()")
    assert -200 <= behind <= -20, behind


def test_a_subclass_a_page_constructs_is_stamped_too(page):
    deltas = page.evaluate(
        "[(() => new Event('x').timeStamp - performance.now())(),"
        " (() => new MouseEvent('x').timeStamp - performance.now())()]")
    assert all(-10 <= delta <= 1 for delta in deltas), deltas


def test_time_origin_still_reads_the_wall_clock(page):
    """The stamp moved onto the document's clock; timeOrigin stays epoch milliseconds."""
    assert page.evaluate("Math.abs(performance.timeOrigin - Date.now()) < 60000") is True


# -- pacing --------------------------------------------------------------------


def test_a_move_without_a_duration_dispatches_its_positions_at_once(page):
    """The default stays a burst, which is what a driver that wants speed asks for."""
    page.evaluate("""(() => { globalThis.times = [];
      addEventListener('mousemove', (e) => times.push(e.timeStamp)); })()""")
    page.mouse.move(50, 20, steps=4)
    times = page.evaluate("times")
    assert len(times) == 4
    assert times[-1] - times[0] < 30


def test_a_move_with_a_duration_spreads_its_positions_over_it(page):
    """Chrome delivers a position per frame; ``duration`` is how a driver asks for that."""
    page.evaluate("""(() => { globalThis.times = [];
      addEventListener('mousemove', (e) => times.push([e.timeStamp, performance.now()])); })()""")
    started = time.perf_counter()
    page.mouse.move(50, 20, steps=4, duration=0.2)
    elapsed = time.perf_counter() - started
    times = page.evaluate("times")
    gaps = [times[index + 1][0] - times[index][0] for index in range(len(times) - 1)]
    assert elapsed >= 0.18, elapsed
    assert len(gaps) == 3 and min(gaps) >= 30, gaps
    # The document's own clock moved with the pauses, which is what makes them real.
    assert times[-1][1] - times[0][1] >= 150, times


def test_a_click_can_hold_the_button_down_for_a_delay(page):
    """A press lasts a moment in Chrome; ``delay`` is the pause before the release."""
    page.evaluate("""(() => { globalThis.down = 0; globalThis.held = 0;
      addEventListener('mousedown', () => { globalThis.down = performance.now(); }, true);
      addEventListener('mouseup', () => { globalThis.held = performance.now() - globalThis.down; },
                       true); })()""")
    page.mouse.click(50, 20, delay=0.06)
    assert page.evaluate("globalThis.held") >= 40


# -- the shape of a move -------------------------------------------------------


def positions(page, action):
    """The positions *action* reports: pointer events keep the fractional coordinates."""
    page.evaluate("""(() => {
      if (!globalThis.recording) {
        globalThis.recording = true;
        addEventListener('pointermove', (e) => globalThis.positions.push([e.clientX, e.clientY]));
      }
      globalThis.positions = [];
    })()""")
    action()
    return page.evaluate("globalThis.positions")


def test_a_straight_move_keeps_to_the_line_between_its_ends(page):
    points = positions(page, lambda: page.mouse.move(120, 80, steps=4))
    assert points[-1] == [120.0, 80.0]
    assert [point[1] * 3 - point[0] * 2 for point in points] == [0.0] * 4


def test_a_human_curve_bows_away_from_that_line_and_lands_on_the_target(page):
    from pyv8c.platform.input import HUMAN_SAMPLES

    points = positions(page, lambda: page.mouse.move(300, 200, curve="human", seed=7))
    assert len(points) == HUMAN_SAMPLES
    assert points[-1] == [300.0, 200.0]
    assert any(abs(point[1] * 3 - point[0] * 2) > 1 for point in points)


def test_a_curve_reports_the_count_it_is_given(page):
    points = positions(page, lambda: page.mouse.move(300, 200, curve="human", steps=5, seed=7))
    assert len(points) == 5
    assert points[-1] == [300.0, 200.0]


def test_the_same_seed_replays_the_same_path(page):
    first = positions(page, lambda: page.mouse.move(300, 200, curve="human", seed=7))
    page.mouse.move(0, 0)
    second = positions(page, lambda: page.mouse.move(300, 200, curve="human", seed=7))
    assert first == second


def test_an_unknown_curve_is_refused(page):
    with pytest.raises(ValueError):
        page.mouse.move(10, 10, curve="spiral")


# -- merged samples ------------------------------------------------------------


#: What each pointer position reports about the samples it stands for.
COALESCED_SHAPES = """(() => { globalThis.shapes = [];
  addEventListener('pointermove', (e) => {
    const merged = e.getCoalescedEvents();
    shapes.push([merged.length, merged[merged.length - 1] === e, e.getPredictedEvents().length]);
  }); })()"""


def test_a_burst_of_positions_arrives_as_one_batch(page):
    """A page reading the merged samples sees the burst so far, its own reading last."""
    page.evaluate(COALESCED_SHAPES)
    page.mouse.move(120, 80, steps=4)
    shapes = page.evaluate("globalThis.shapes")
    assert [shape[0] for shape in shapes] == [1, 2, 3, 4]
    assert shapes[-1][1] is True
    assert {shape[2] for shape in shapes} == {0}


def test_a_paced_move_merges_nothing(page):
    page.evaluate(COALESCED_SHAPES)
    page.mouse.move(120, 80, steps=3, duration=0.15)
    shapes = page.evaluate("globalThis.shapes")
    assert [shape[0] for shape in shapes] == [1, 1, 1]
    assert {shape[1] for shape in shapes} == {True}


def test_only_pointer_events_carry_merged_samples(page):
    """Chrome's mouse events have no such reading either."""
    assert page.evaluate("typeof MouseEvent.prototype.getCoalescedEvents") == "undefined"


def test_other_pointer_events_carry_none(page):
    """Chrome reports an empty list for the pointer events that are not moves."""
    page.evaluate("""(() => { globalThis.shapes = [];
      for (const type of ['pointerdown', 'pointerup'])
        addEventListener(type, (e) => shapes.push([type, e.getCoalescedEvents().length])); })()""")
    page.mouse.click(50, 20)
    assert page.evaluate("globalThis.shapes") == [["pointerdown", 0], ["pointerup", 0]]
