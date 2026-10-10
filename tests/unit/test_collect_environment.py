"""The collector: what it refuses, and how it agrees with the field table."""
import json
import re

import pytest

pytest.importorskip("pyv8", reason="native pyv8 extension is not importable")

from pyv8c.engine.idl import ArrayProto  # noqa: E402
from pyv8c.foundation.environment import COLLECTED_KEYS, BrowserEnvironment
from pyv8c.tools import collect_environment as collector
from pyv8c.web.dom.media_source import (families_in, parse_type, probe_candidates,
                                        probe_containers, probe_families)

DOTTED_LITERAL = re.compile(r"'([a-z0-9_]+(?:\.[a-z0-9_]+)+)'")


def payload(**overrides):
    """A complete collected profile, as the browser would answer."""
    resolved = BrowserEnvironment().resolved()
    return {name: resolved[name] for name in COLLECTED_KEYS} | overrides


#: A page cannot enumerate extensions, so the collector reads that one from the
#: browser's own profile instead of asking the document (``profile_extensions``).
HOST_READ_KEYS = ("extensions",)


def test_the_script_asks_for_every_key_the_table_collects():
    asked = set(DOTTED_LITERAL.findall(collector.COLLECT_SCRIPT))
    dotted = {name for name in COLLECTED_KEYS if "." in name}
    assert asked == dotted
    for name in COLLECTED_KEYS:
        if name in HOST_READ_KEYS:
            assert f"'{name}'" not in collector.COLLECT_SCRIPT
            continue
        assert f"'{name}'" in collector.COLLECT_SCRIPT


def test_every_media_probe_asks_about_one_family_the_parser_can_read():
    """The probes are the parser's vocabulary: one container, one family per string."""
    probes = probe_candidates()
    assert list(probes) == list(probe_containers())
    read = set()
    for container, texts in probes.items():
        assert texts[0] == container
        for text in texts[1:]:
            assert parse_type(text)[0] == container
            families = families_in(text)
            assert len(families) == 1, text
            read.add(families[0])
    assert read == set(probe_families())


def test_the_collection_script_carries_the_parser_vocabulary():
    assert "__MEDIA_CANDIDATES__" not in collector.COLLECT_SCRIPT
    for container in probe_containers():
        assert f'"{container}"' in collector.COLLECT_SCRIPT


def test_devtools_port_is_read_from_the_file_chrome_writes(tmp_path):
    (tmp_path / "DevToolsActivePort").write_text("49321\n/devtools/browser/abc\n", encoding="utf-8")
    assert collector.read_devtools_port(tmp_path, timeout=0.5) == (49321, "/devtools/browser/abc")

    with pytest.raises(collector.CollectionError, match="DevToolsActivePort"):
        collector.read_devtools_port(tmp_path / "elsewhere", timeout=0.05)


def test_the_browser_is_located_by_flag_then_environment_then_search(tmp_path, monkeypatch):
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    monkeypatch.setenv(collector.CHROME_ENV, str(browser))
    assert collector.find_chrome() == browser
    assert collector.find_chrome(str(browser)) == browser

    with pytest.raises(collector.CollectionError, match="does not exist"):
        collector.find_chrome(str(tmp_path / "missing"))

    monkeypatch.delenv(collector.CHROME_ENV)
    monkeypatch.setattr(collector, "CHROME_PATHS", (str(tmp_path / "absent"),))
    with pytest.raises(collector.CollectionError, match=collector.CHROME_ENV):
        collector.find_chrome()

    monkeypatch.setenv(collector.CHROME_ENV, str(tmp_path / "absent"))
    with pytest.raises(collector.CollectionError, match="does not exist"):
        collector.find_chrome()


def test_a_profile_has_to_be_complete_before_it_is_written():
    environment = collector.build_environment(payload(**{"platform": "Win32"}))
    assert environment.platform == "Win32"
    assert set(environment.to_dict()) == set(COLLECTED_KEYS)

    with pytest.raises(collector.CollectionError, match="missing \\['user_agent'\\]"):
        collector.build_environment({name: 1 for name in COLLECTED_KEYS if name != "user_agent"})
    with pytest.raises(collector.CollectionError, match="unknown \\['screen.nope'\\]"):
        collector.build_environment(payload(**{"screen.nope": 1}))
    with pytest.raises(TypeError, match="timezone"):
        collector.build_environment(payload(**{"timezone": None}))


def test_a_run_writes_the_environment_and_the_surface_together():
    """Both facts are read from one session, so they land in one document."""
    collected = payload()
    surface = collector.build_surface(
        {"keys": ["a"], "own": ["a", "b"],
         "prototypes": {"Node": ["appendChild", "cloneNode"], "Event": []},
         "objects": {"CSS": ["escape", "supports"], "Node": ["Node"]},
         "iterators": {"Node": {"iterator": 1, "four": 4}, "CSS": {"iterator": 0, "four": 0}}},
        {"Node": {"prototype": {"appendChild", "append", "cloneNode"},
                  "interface": {"Node", "ELEMENT_NODE"}},
         "CSS": {"prototype": set(), "interface": {"escape", "supports", "animationWorklet"}}},
        "digest-of-the-baseline")
    document = collector.build_profile(collected, surface, source={"browser": "Chrome/154.0.8037.95"})

    assert document["bindings"] == {
        "keys": ["a"], "exposed": ["a", "b"],
        "omits": {
            "prototype": {"Node": ["append"]},
            # An interface object answers for its own members, where a namespace keeps them.
            "interface": {"CSS": ["animationWorklet"], "Node": ["ELEMENT_NODE"]}},
        # What each interface borrows from Array.prototype, in the field's own vocabulary.
        "array_protos": {"Node": ArrayProto.iterable},
        "against": "digest-of-the-baseline"}
    assert document["source"]["browser"] == "Chrome/154.0.8037.95"
    assert set(document) == {"schema", "source", "bindings", *COLLECTED_KEYS}
    # The section is not an environment key: the profile still loads as one.
    assert BrowserEnvironment.from_dict(document).to_dict() == collected


def test_a_surface_without_gap_readings_writes_empty_sections():
    """A browser that answered nothing about its prototypes narrows no interface."""
    surface = collector.build_surface({"keys": ["a"], "own": ["a"]},
                                      {"Node": {"prototype": {"appendChild"}}})

    assert surface["omits"]["prototype"] == {}
    assert surface["omits"]["interface"] == {}
    assert surface["array_protos"] == {}
    assert surface["against"] == ""


def test_only_a_declared_name_can_be_a_gap():
    """What the baseline does not declare is not a difference, so it is not recorded."""
    surface = collector.build_surface({"keys": [], "own": [], "prototypes": {"Node": [],
                                                                            "Unknown": ["x"]},
                                       "objects": {"Unknown": ["x"]}},
                                      {"Node": {"prototype": {"appendChild"}}})

    assert surface["omits"]["prototype"] == {"Node": ["appendChild"]}
    assert surface["omits"]["interface"] == {}


def test_a_location_is_only_compared_with_its_own_reading():
    """A prototype name is not evidence about the interface object, or the other way round."""
    surface = collector.build_surface(
        {"keys": [], "own": [],
         "prototypes": {"CSS": ["animationWorklet"]},
         "objects": {"CSS": ["supports"]}},
        {"CSS": {"prototype": {"animationWorklet"}, "interface": {"supports", "escape"}}})

    assert surface["omits"]["prototype"] == {}
    assert surface["omits"]["interface"] == {"CSS": ["escape"]}


def test_the_array_value_is_the_shape_the_browser_publishes():
    """The field states the iterator alone or that iterator with all four, and nothing else.

    A prototype that answers no ``@@iterator`` records nothing -- which is how ``Cache`` (whose
    ``keys`` is an async method) and ``CSSMathMax`` (whose ``values`` is a length) read too, so a
    member that merely shares a name cannot invent a word.  A name the baseline never declared is
    not a difference, the same rule the gaps follow.
    """
    declared = {"NodeList": {"prototype": set()}, "HTMLCollection": {"prototype": set()},
                "Cache": {"prototype": set()}}
    protos = collector._array_protos(
        {"NodeList": {"iterator": 1, "four": 4},
         "HTMLCollection": {"iterator": 1, "four": 0},
         "Cache": {"iterator": 0, "four": 1},
         "Unknown": {"iterator": 1, "four": 4}},
        set(declared))

    assert protos == {"NodeList": ArrayProto.iterable, "HTMLCollection": ArrayProto.iterator}


def test_a_shape_no_word_carries_stops_the_run():
    """The iterator with *some* of the four is not a shape ``array`` can state, so say so.

    Measured against Chrome 154 on 2026-10-07 no interface the baseline declares is in that
    position -- ``Iterator.prototype`` answers ``@@iterator`` with only ``forEach``, and it is a
    V8 built-in the baseline does not declare -- so this is the guard that keeps the two words
    honest if a release adds such an interface, not a case to paper over.
    """
    reading = {"iterators": {"NodeList": {"iterator": 1, "four": 2}}}
    with pytest.raises(collector.CollectionError, match="@@iterator together with 2 of"):
        collector.build_surface(dict(reading, keys=[], own=[]),
                                {"NodeList": {"prototype": set()}})
    # …and it is not raised for an interface the baseline does not know.
    surface = collector.build_surface(dict(reading, keys=[], own=[]), {})
    assert surface["array_protos"] == {}


def test_a_non_finite_number_is_written_as_its_name():
    """A bare ``Infinity`` is not JSON, so the profile spells it out."""
    collected = payload(**{"battery.discharging_time": float("inf")})
    document = collector.build_profile(collected, collector.build_surface({"keys": [], "own": []}),
                                       source={"browser": "Chrome/1"})

    assert document["battery.discharging_time"] == "Infinity"
    assert json.dumps(document, allow_nan=False)


def test_the_script_reads_the_facts_a_page_would_and_names_the_push_descriptor():
    """Each key is asked for in the page, and the permission vocabulary is substituted."""
    script = collector.COLLECT_SCRIPT
    for key in ("viewport.outer_inset_width", "viewport.outer_inset_height", "media.constraints",
                "speech.voices", "permissions.names", "permissions.granted",
                "permissions.spec_names", "gpu.wgsl_features"):
        assert f"'{key}'" in script, key
    assert "__PERMISSION_NAMES__" not in script
    # Chrome refuses the push permission unless the descriptor says userVisibleOnly.
    assert "name === 'push'" in script
    assert '"push"' in script


def test_the_surface_script_asks_for_what_the_section_carries():
    assert "Object.keys(window)" in collector.SURFACE_SCRIPT
    assert "Object.getOwnPropertyNames(window)" in collector.SURFACE_SCRIPT
    assert "getOwnPropertyNames(prototype)" in collector.SURFACE_SCRIPT
    assert "prototypes" in collector.SURFACE_SCRIPT
    # The interface object too: a namespace keeps its own members there and has no prototype.
    assert "getOwnPropertyNames(value)" in collector.SURFACE_SCRIPT
    # And the iterator machinery: ``@@iterator`` itself, not a member that shares a name.
    assert "Symbol.iterator" in collector.SURFACE_SCRIPT
    assert "iterators" in collector.SURFACE_SCRIPT
    assert "objects" in collector.SURFACE_SCRIPT


def test_the_default_output_is_the_profile_the_package_ships():
    assert collector.DEFAULT_OUTPUT.name == "chrome-environment.json"
    assert collector.DEFAULT_OUTPUT.parent.name == "data"
    assert collector.DEFAULT_OUTPUT.exists()


def install_extension(profile, identifier, manifest, reasons=()):
    """One extension directory the collector reads, and what the browser says about it."""
    version = profile / "Extensions" / identifier / "1.0.0_0"
    version.mkdir(parents=True)
    (version / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"disable_reasons": list(reasons)}


def write_settings(profile, settings):
    (profile / "Secure Preferences").write_text(
        json.dumps({"extensions": {"settings": settings}}), encoding="utf-8")


def test_a_profile_reports_only_the_extensions_that_answer_a_page(tmp_path):
    """Enabled and exposing something is what a page can read; a suspended one answers none."""
    profile = tmp_path / "Default"
    settings = {
        "cneomjecgakdfoeehmmmoiklncdiodmh": install_extension(profile,
            "cneomjecgakdfoeehmmmoiklncdiodmh",
            {"manifest_version": 3, "web_accessible_resources": [
                {"resources": ["/icons/icon_64.png"], "matches": ["https://*/*"]}]}),
        "nmmhkkegccagdldgiimedpiccmgmieda": install_extension(profile,
            "nmmhkkegccagdldgiimedpiccmgmieda",
            {"manifest_version": 2, "web_accessible_resources": ["branding.css"]}),
        "aapbdbdomjkkjkaonfhkkikfgjllcleb": install_extension(profile,
            "aapbdbdomjkkjkaonfhkkikfgjllcleb",
            {"manifest_version": 3, "web_accessible_resources": [
                {"resources": ["popup_css_compiled.css"], "matches": ["<all_urls>"]}]},
            reasons=(1,)),
        "ojfebgpkimhlhcblbalbfjblapadhbol": install_extension(profile,
            "ojfebgpkimhlhcblbalbfjblapadhbol", {"manifest_version": 3}),
    }
    write_settings(profile, settings)
    assert collector.profile_extensions(tmp_path) == {
        "cneomjecgakdfoeehmmmoiklncdiodmh": [
            {"resources": ["/icons/icon_64.png"], "matches": ["https://*/*"]}],
        "nmmhkkegccagdldgiimedpiccmgmieda": [
            {"resources": ["branding.css"], "matches": ["<all_urls>"]}],
    }


def test_a_user_data_dir_can_be_named_or_one_profile_inside_it(tmp_path):
    profile = tmp_path / "Profile 1"
    write_settings(profile, {"cneomjecgakdfoeehmmmoiklncdiodmh": install_extension(profile,
        "cneomjecgakdfoeehmmmoiklncdiodmh",
        {"manifest_version": 3, "web_accessible_resources": [{"resources": ["a.css"]}]})})
    assert collector.profile_extensions(tmp_path, profile="Profile 1") == {
        "cneomjecgakdfoeehmmmoiklncdiodmh": [
            {"resources": ["a.css"], "matches": ["<all_urls>"]}]}
    # The same directory named on its own: no ``Extensions`` one level in.
    assert collector.profile_extensions(profile) == collector.profile_extensions(tmp_path, profile="Profile 1")


def test_the_collected_payload_carries_the_extension_table():
    environment = collector.build_environment(payload())
    assert "extensions" in environment.to_dict()


def test_a_failed_collection_exits_nonzero_with_the_reason(monkeypatch, capsys):
    def refuse(explicit=None):
        raise collector.CollectionError("no Chrome found")

    monkeypatch.setattr(collector, "find_chrome", refuse)
    assert collector.main([]) == 1
    assert "error: no Chrome found" in capsys.readouterr().err
