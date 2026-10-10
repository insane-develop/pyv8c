"""The policy gates' own tests: a gate that cannot fail is not a gate."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "check_policy.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("check_policy", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return load_tool()


def test_a_change_falls_into_the_bucket_that_decides_its_rule(tool):
    assert tool.classify("pyv8c/web/dom/document.py") == "implementation"
    assert tool.classify("pyv8c/idl/interfaces.zip") == "data"
    assert tool.classify("tests/unit/test_check_policy.py") == "test"
    assert tool.classify("docs/ARCHITECTURE.md") == "docs"
    assert tool.classify("README.md") == "docs"
    assert tool.classify("tools/check_policy.py") == "tooling"


def test_an_implementation_change_without_a_test_is_reported(tool):
    findings = tool.companion_findings(
        ["pyv8c/platform/browser.py", "tools/check_policy.py"]
    )
    assert findings == ["pyv8c/platform/browser.py"]


def test_a_test_alongside_silences_the_rule(tool):
    assert tool.companion_findings(
        ["pyv8c/platform/browser.py", "tests/browser/test_page.py"]
    ) == []


def test_data_and_documentation_changes_need_no_companion(tool):
    assert tool.companion_findings(["pyv8c/idl/interfaces.zip"]) == []
    assert tool.companion_findings(["docs/ARCHITECTURE.md", "README.md"]) == []
    assert tool.companion_findings([]) == []


def test_the_companion_gate_fails_only_when_asked_to_be_strict(tool, monkeypatch):
    monkeypatch.setattr(tool, "changed_paths", lambda base: ["pyv8c/engine/stubs.py"])
    assert tool.gate_companions("HEAD", strict=False) is True
    assert tool.gate_companions("HEAD", strict=True) is False


def test_the_startup_budget_allows_the_tolerance_and_no_more(tool):
    baseline = {"median_ms": 100.0}
    budget = tool.startup_budget(baseline)
    assert budget == pytest.approx(115.0)
    assert 114.0 <= budget
    assert 116.0 > budget


def test_the_budget_catches_one_import_put_back_on_the_startup_path(tool):
    """The tolerance has to be tighter than the cost this gate exists to catch.

    Measured on this tree: a clean startup is about 155 ms, and importing loguru
    again in a startup-path module took it to 190 ms.
    """
    budget = tool.startup_budget({"median_ms": 155.1})
    assert budget < 190.7
    assert budget > 160.0


def test_the_startup_gate_fails_on_a_regression(tool, monkeypatch, tmp_path):
    baseline = tmp_path / "startup_baseline.json"
    baseline.write_text(json.dumps({"median_ms": 150.0}), encoding="utf-8")
    monkeypatch.setattr(tool, "BASELINE_FILE", baseline)

    monkeypatch.setattr(tool, "measure_startup", lambda runs=3, timeout=300: (160.0, 165.0, 253))
    assert tool.gate_startup(runs=3, record=False) is True

    monkeypatch.setattr(tool, "measure_startup", lambda runs=3, timeout=300: (200.0, 240.0, 334))
    assert tool.gate_startup(runs=3, record=False) is False


def test_a_missing_baseline_does_not_fail_the_run(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "BASELINE_FILE", tmp_path / "absent.json")
    assert tool.gate_startup(runs=1, record=False) is True


def test_the_command_runs_on_a_clean_tree(tool):
    result = subprocess.run(
        [sys.executable, str(TOOL), "--gates", "companions"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "companions" in result.stdout
    assert result.stdout.strip().endswith("gate(s) clean")


def test_an_unknown_gate_is_rejected(tool):
    result = subprocess.run(
        [sys.executable, str(TOOL), "--gates", "layers,nonsense"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "unknown gate" in result.stderr


@pytest.mark.slow  # runs the whole unit suite again, through the gate
@pytest.mark.skipif(
    os.environ.get("PYV8C_OFFLINE") == "1",
    reason="the offline gate runs this suite; the nested run must not recurse",
)
def test_the_offline_gate_holds_the_unit_suite_to_its_promise(tool):
    """``tests/unit`` must import nothing native: that is where offline runs it."""
    assert tool.gate_offline() is True


COPY = (0.94, "tests/browser/test_copy.py", 12, "test_copy", "a_case", 34)


def test_a_test_that_repeats_a_corpus_case_is_reported(tool, monkeypatch, capsys):
    monkeypatch.setattr(tool, "duplicate_findings", lambda: [COPY])
    assert tool.gate_duplicates(strict=False) is True
    assert "tests/browser/test_copy.py:12" in capsys.readouterr().out


def test_the_duplicate_gate_fails_only_when_asked_to_be_strict(tool, monkeypatch):
    monkeypatch.setattr(tool, "duplicate_findings", lambda: [COPY])
    assert tool.gate_duplicates(strict=True) is False


@pytest.mark.slow  # scans every test's script for corpus overlap
def test_no_test_repeats_a_corpus_case(tool):
    """The corpus is where page behaviour lives, and Chrome is run against it."""
    assert tool.duplicate_findings() == []
