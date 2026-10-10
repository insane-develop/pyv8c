"""The corpus, checked here and against the Chrome installed on this machine.

Two passes over one list of cases.  The first holds pyv8c to what the corpus
declares, so a behaviour that changes without anyone deciding to change it fails.
The second runs the same page in the local Chrome and reports every difference --
except the ones a case has an explanation for, which are reported as excluded rather
than scored.  There is no single number: a run says which cases agreed, which
excluded with what reason, and which diverged.
"""
from __future__ import annotations

import importlib

import pytest

from corpus import CASES
from harness import (
    ReferenceUnavailable,
    chrome_executable,
    chrome_reference,
    divergence,
    observe_here,
)

pytest.importorskip("pyv8", reason="the corpus runs in the native environment")

NAMES = [case.name for case in CASES]

OPTIONAL_REQUIREMENTS = {"pillow": "PIL"}


def missing_requirement(case):
    """Why a case cannot run here, or ``""`` when it can."""
    module = OPTIONAL_REQUIREMENTS.get(case.requires)
    if module is None:
        return ""
    try:
        importlib.import_module(module)
    except ImportError as missing:
        return f"{case.requires} is not importable: {missing}"
    return ""


@pytest.fixture(scope="module")
def reference():
    """One local Chrome for the module, or a skip saying what is missing."""
    try:
        with chrome_reference() as session:
            yield session
    except ReferenceUnavailable as unavailable:
        pytest.skip(str(unavailable))


def test_the_corpus_keeps_its_own_shape():
    """A corpus is only as good as the rules it holds itself to."""
    assert len(NAMES) == len(set(NAMES)), "case names double as test ids, so they are unique"
    for case in CASES:
        assert case.script.strip(), case.name
        assert bool(case.expect_error) != (case.expected is not None), (
            f"{case.name} declares an expected value or an expected error, not both"
        )
        assert not case.requires or case.requires in OPTIONAL_REQUIREMENTS, (
            f"{case.name} asks for an optional dependency the runners do not know: {case.requires}"
        )


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_pyv8c_observes_what_the_case_declares(case):
    if reason := missing_requirement(case):
        pytest.skip(reason)
    observed = observe_here(case)
    if case.expect_error:
        assert observed.error == case.expect_error
    else:
        assert observed.error == "", f"{case.name} threw: {observed}"
        assert observed.value == case.expected


def test_the_reference_is_the_chrome_installed_here(reference):
    """The comparison is against this machine's browser, never a downloaded one."""
    installed, reason = chrome_executable()
    assert installed is not None, reason
    assert reference.executable == installed
    assert reference.version


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_chrome_agrees(case, reference):
    if reason := missing_requirement(case):
        pytest.skip(reason)
    here = observe_here(case)
    there = reference.observe(case)
    difference = divergence(here, there, reference.version)
    if case.known_divergence:
        assert difference, (
            f"{case.name} no longer diverges from Chrome {reference.version} ({there});"
            f" drop known_divergence: {case.known_divergence}"
        )
        pytest.skip(f"excluded (Chrome {reference.version}): {case.known_divergence}")
    assert not difference, f"{case.name}: {difference}"
