"""Running against a baseline from another Chromium version.

``baseline_mismatch="warn"`` (the default) reports a difference once and skips
what the baseline does not describe; ``"error"`` refuses it instead.
"""
import copy
import json
from pathlib import Path

import pytest
from loguru import logger

from pyv8c import Browser
from pyv8c.engine.idl import Idl

#: A member the class declares and an older baseline does not have: an ``EventTarget`` handler
#: slot, which is stated here rather than by the interface it is answered through.
EXTRA_MEMBER = ("EventTarget", "onmouseenter")


def baseline_without_member(tmp_path):
    """The bundled IDL with one member dropped everywhere, as an older one would be."""
    path = tmp_path / "idl.json"
    idl = Idl.load(profile=None)
    document = {"idl": dict(idl.metadata), "interfaces": copy.deepcopy(dict(idl.interfaces))}
    for interface in document["interfaces"].values():
        members = interface.get("members") if isinstance(interface, dict) else None
        if not members:
            continue
        for location in ("instance", "prototype", "interface"):
            members[location] = [
                entry for entry in members.get(location, ())
                if entry.get("name") != EXTRA_MEMBER[1]
            ]
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_missing_member_stops_the_page_in_error_mode(tmp_path):
    browser = Browser(
        idl_path=baseline_without_member(tmp_path),
        baseline_mismatch="error", stub_logging="off",
    )
    try:
        with pytest.raises(TypeError, match="onmouseenter"):
            browser.new_context().new_page()
    finally:
        browser.close()


def test_warn_mode_skips_the_member_instead(tmp_path):
    browser = Browser(
        idl_path=baseline_without_member(tmp_path),
        baseline_mismatch="warn", stub_logging="off",
    )
    try:
        page = browser.new_context().new_page()
        page.set_content("<body></body>", url="https://warn.test/")
        # The member is not in this baseline, so the declaration that installs it is skipped.
        assert page.evaluate("typeof window.onmouseenter") == "undefined"
    finally:
        browser.close()


def test_warn_mode_reports_the_mismatch_once(tmp_path):
    messages = []
    token = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        browser = Browser(
            idl_path=baseline_without_member(tmp_path),
            baseline_mismatch="warn", stub_logging="on",
        )
        try:
            browser.new_context().new_page()
        finally:
            browser.close()
        reported = [message for message in messages
                    if "onmouseenter" in message and "interface baseline" in message]
        assert len(reported) == 1, reported
    finally:
        logger.remove(token)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="baseline_mismatch"):
        Browser(baseline_mismatch="quiet")
