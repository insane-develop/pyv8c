"""The measured profile narrows a document to what the reference browser installs."""
import json

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.idl import ARRAY_PROTOS, PROFILE_PATH, ArrayProto, Idl, Profile  # noqa: E402


def collection(baseline, surface=None):
    """The same document under one profile, so a test can narrow it as it likes."""
    return Idl(baseline.path, baseline.digest, baseline.metadata, baseline.interfaces, surface)


@pytest.fixture(scope="module")
def baseline():
    return Idl.load()


def test_a_window_reading_narrows_window_members(baseline):
    surface = Profile(browser="test", exposed=frozenset({"onclick"}))
    table = collection(baseline, surface=surface)

    assert {member.name for _location, member in table.own_members("Window")} == {"onclick"}
    assert table.member("Window", "sharedStorage", "attribute") is None


def test_a_measured_value_overrides_the_baselines(baseline):
    """The baseline carries every platform state; the profile says what this release installs.

    ``NodeList`` borrows the iterator with the four named methods and ``HTMLCollection`` the
    iterator alone, both measured against Chrome 154 on 2026-10-07.  A release that published no
    iterator for one of them records nothing, which is how the value in force becomes *narrower*
    than the baseline's rather than wider.
    """
    assert collection(baseline).interface("NodeList").array == ArrayProto.iterable
    assert collection(baseline).interface("HTMLCollection").array == ArrayProto.iterator

    surface = Profile(browser="test", exposed=frozenset(),
                            array_protos={"NodeList": ArrayProto.iterator,
                                          "Unknown": ArrayProto.iterable})
    table = collection(baseline, surface=surface)

    assert table.interface("NodeList").array == ArrayProto.iterator
    # An interface the profile says nothing about keeps the baseline's word鈥?
    assert table.interface("HTMLCollection").array == ArrayProto.iterator
    # 鈥nd a name the baseline never declared has no interface to give a word to.
    assert "Unknown" not in table.names()


def test_a_profile_of_another_release_does_not_apply_its_values(baseline):
    """A measurement describes one release, so a digest that does not match says nothing."""
    surface = Profile(browser="test", exposed=frozenset(),
                            array_protos={"NodeList": ArrayProto.iterator},
                            against="another-release")
    table = collection(baseline, surface=surface)

    assert table.interface("NodeList").array == ArrayProto.iterable


def test_a_surface_without_prototype_readings_leaves_other_interfaces_whole(baseline):
    """A profile written before the prototype section existed must not lose any member."""
    surface = Profile(browser="test", exposed=frozenset({"onclick"}))
    table = collection(baseline, surface=surface)

    assert len(table.own_members("Document")) == len(
        collection(baseline).own_members("Document")
    )


def test_a_prototype_gap_narrows_only_that_location(baseline):
    """A gap names members the browser leaves out; other locations are not prototypes."""
    raw = collection(baseline).own_members("Document")
    prototype = {member.name for location, member in raw if location == "prototype"}
    other = {(location, member.name) for location, member in raw if location != "prototype"}
    assert prototype and other, "this test needs an interface with both kinds of member"
    omitted = sorted(prototype)[0]

    surface = Profile(browser="test", exposed=frozenset({"onclick"}),
                            omitted_prototype={"Document": frozenset({omitted})})
    table = collection(baseline, surface=surface)

    kept = {(location, member.name) for location, member in table.own_members("Document")}
    assert ("prototype", omitted) not in kept
    assert kept == other | {("prototype", name) for name in prototype - {omitted}}


def test_an_interface_the_browser_was_not_asked_about_keeps_every_location(baseline):
    surface = Profile(browser="test", exposed=frozenset({"onclick"}),
                            omitted_prototype={"Document": frozenset({"querySelector"})})

    assert len(collection(baseline, surface=surface).own_members("Node")) == len(
        collection(baseline).own_members("Node")
    )


def test_a_gap_only_applies_to_the_baseline_it_was_measured_against(baseline):
    """The names mean nothing against another release, so they are dropped, not applied."""
    elsewhere = Profile(browser="test", exposed=frozenset({"onclick"}),
                              omitted_prototype={"Document": frozenset({"querySelector"})},
                              against="measured-against-another-release")
    measured = Profile(browser="test", exposed=frozenset({"onclick"}),
                             omitted_prototype={"Document": frozenset({"querySelector"})},
                             against=baseline.digest)

    ignored = collection(baseline, surface=elsewhere).own_members("Document")
    applied = collection(baseline, surface=measured).own_members("Document")

    assert "querySelector" in {member.name for _location, member in ignored}
    assert "querySelector" not in {member.name for _location, member in applied}


def test_a_gap_stops_the_descendant_search():
    """A name the browser leaves out is not recovered from a subclass."""
    members = {"prototype": [{"name": "omitted", "kind": "attribute"}]}
    baseline = Idl(path="test", digest="", metadata={}, interfaces={
        "Base": {"parent": None, "members": {"prototype": []}, "constants": []},
        "Child": {"parent": "Base", "members": members, "constants": []},
    })
    surface = Profile("test", frozenset({"Base"}), omitted_prototype={"Base": frozenset({"omitted"})})
    table = collection(baseline, surface=surface)

    assert table.member("Child", "omitted", "attribute") is not None
    assert table.member("Base", "omitted", "attribute") is None


def test_a_member_the_browser_leaves_out_is_still_declared(baseline):
    """Implementing it describes a difference between browsers, not a baseline one."""
    surface = Profile.load()
    assert surface is not None
    if not surface.omitted_prototype:
        pytest.skip("the shipped profile carries no prototype gaps yet")
    table = collection(baseline, surface=surface)

    assert table.member("Element", "ariaOwnsElements", "attribute") is None
    assert table.declares("Element", "ariaOwnsElements", "attribute")
    assert not table.declares("Element", "noSuchMember", "attribute")


def test_without_a_surface_every_baseline_member_stays(baseline):
    assert collection(baseline).member("Window", "sharedStorage", "attribute") is not None


def test_a_surface_narrows_which_interfaces_a_window_installs(baseline):
    surface = Profile(browser="test", exposed=frozenset({"EventTarget", "Node", "Event"}))
    table = collection(baseline, surface=surface)

    assert set(table.exposed("Window")) == {"EventTarget", "Node", "Event"}
    assert len(table.exposed("Worker")) == len(collection(baseline).exposed("Worker"))


def test_window_aliases_need_the_baseline_field_and_the_reference_alike():
    baseline = Idl(path="test", digest="", metadata={}, interfaces={
        "URL": {"parent": None, "tag": "URL", "exposed_in": ["Window"], "aliases": ["webkitURL"],
                "constructor": {"exposed": True}, "template": {}, "members": {}, "constants": []},
    })
    both = collection(baseline, surface=Profile("test", frozenset({"URL", "webkitURL"})))
    only_target = collection(baseline, surface=Profile("test", frozenset({"URL"})))

    assert both.window_aliases() == {"webkitURL": "URL"}
    assert only_target.window_aliases() == {}


def test_the_committed_surface_drops_the_members_no_browser_installs(baseline):
    surface = Profile.load()
    assert surface is not None and surface.browser

    table = collection(baseline, surface=surface)
    names = {member.name for _location, member in table.own_members("Window")}
    assert not names & {"sharedStorage", "printing", "ontouchstart", "testOriginTrialGlobalAttribute"}
    assert names <= surface.exposed

    # The same reading covers the prototypes, when the profile carries the gaps.
    if not surface.omitted_prototype:
        pytest.skip("the shipped profile carries no prototype gaps yet")
    element = {member.name for _location, member in table.own_members("Element")}
    assert not element & {"computedRole", "headingReset", "containerTiming", "afterHTML"}
    assert {"id", "slot", "part", "attributes"} <= element

    # Nothing the browser was measured to leave out is installed from this reading.
    for interface in ("Element", "Document", "HTMLElement", "HTMLIFrameElement"):
        omitted = surface.omitted_prototype.get(interface, frozenset())
        installed = {member.name for location, member in table.own_members(interface)
                     if location == "prototype"}
        assert not installed & omitted


def test_the_surface_is_a_section_of_the_profile_the_environment_reads():
    """One collection run states both, so the file and the reader cannot disagree."""
    from pyv8c.foundation.environment import PROFILE

    assert PROFILE_PATH == PROFILE
    payload = json.loads(PROFILE.read_text(encoding="utf-8"))
    surface = Profile.load()

    assert surface is not None
    assert surface.browser == payload["source"]["browser"]
    section = payload["bindings"]
    assert surface.exposed == frozenset(section["exposed"])
    omits = section.get("omits") or {}
    assert surface.omitted_prototype == {name: frozenset(names)
                                         for name, names in (omits.get("prototype") or {}).items()}
    assert surface.omitted_interface == {name: frozenset(names)
                                         for name, names in (omits.get("interface") or {}).items()}
    assert surface.array_protos == {name: bits
                                    for name, bits in (section.get("array_protos") or {}).items()
                                    if bits in ARRAY_PROTOS}
    assert surface.against == section.get("against", "")


def test_a_profile_without_a_usable_section_reads_none(tmp_path):
    names = ("absent", "empty", "wrongly shaped")
    for label in names:
        section = {"absent": None, "empty": {"exposed": []},
                   "wrongly shaped": {"exposed": "Object"}}[label]
        payload = {"schema": 1, "source": {"browser": "Chrome/1"}}
        if section is not None:
            payload["bindings"] = section
        path = tmp_path / f"{label.replace(' ', '-')}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert Profile.load(path) is None, label


def test_a_section_without_gaps_reads_as_no_measurements(tmp_path):
    """An older profile keeps every interface whole, which is the behavior it was read for."""
    payload = {"schema": 1, "source": {"browser": "Chrome/1"},
               "bindings": {"keys": ["a"], "exposed": ["a"]}}
    path = tmp_path / "no-gaps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    surface = Profile.load(path)

    assert surface is not None
    assert surface.omitted_prototype == {}
    assert surface.against == ""


def test_an_interface_gap_narrows_the_interface_object_only(baseline):
    """``CSS`` carries ``animationWorklet`` on itself, not on a prototype, and Chrome leaves it
    out -- measured against Chrome 154 on 2026-10-07 with ``examples/5s/script/surface_diff.py``
    (which is how the two extra getters were found at all)."""
    raw = collection(baseline).own_members("CSS")
    on_object = {member.name for location, member in raw if location == "interface"}
    on_prototype = {member.name for location, member in raw if location == "prototype"}
    assert "animationWorklet" in on_object, "this test needs the baseline as it is"
    assert on_object & {"supports", "escape"}, "and the members Chrome does install"

    surface = Profile(browser="test", exposed=frozenset({"CSS"}),
                            omitted_interface={"CSS": frozenset({"animationWorklet"})})
    table = collection(baseline, surface=surface)

    kept = {member.name for _location, member in table.own_members("CSS")}
    assert "animationWorklet" not in kept
    assert {"supports", "escape"} <= kept
    # A prototype name is not evidence about the interface object, so it stays where it was.
    assert table.member("CSS", "animationWorklet", "operation") is None
    assert on_prototype == {member.name for location, member in table.own_members("CSS")
                            if location == "prototype"}


def test_a_wrongly_shaped_gap_entry_is_not_trusted(tmp_path):
    payload = {"schema": 1, "source": {"browser": "Chrome/1"},
               "bindings": {"keys": ["a"], "exposed": ["a"],
                            "omits": {"prototype": {"Element": "slot", "Node": ["appendChild"]}}}}
    path = tmp_path / "broken-gaps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    surface = Profile.load(path)

    assert surface is not None
    assert surface.omitted_prototype.get("Element") is None
    assert surface.omitted_prototype["Node"] == frozenset({"appendChild"})
