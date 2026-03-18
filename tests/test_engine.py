"""Tests for the lint engine and CLI scaffolding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from toolint.core.models import LintConfig, LintResult, Severity
from toolint.engine import LintEngine
from toolint.formatters import format_json, format_text


def _dummy_checker_pass(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    return []


def _dummy_checker_fail(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    return [
        LintResult(
            rule_id="ATL999",
            severity=Severity.ERROR,
            message="Test error",
            file="test.py",
            line=1,
        )
    ]


def test_engine_register_and_check(tmp_path: Path) -> None:
    # Create minimal pyproject.toml
    (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname = "test-pkg"\n')
    (tmp_path / "test_pkg").mkdir()

    engine = LintEngine()
    engine.register(
        "ATL999",
        name="test-rule",
        description="A test rule",
        severity=Severity.ERROR,
        layer="test",
        checker=_dummy_checker_pass,
    )

    results = engine.check(tmp_path)
    assert results == []


def test_engine_collects_errors(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname = "test-pkg"\n')

    engine = LintEngine()
    engine.register(
        "ATL999",
        name="test-rule",
        description="A test rule",
        severity=Severity.ERROR,
        layer="test",
        checker=_dummy_checker_fail,
    )

    results = engine.check(tmp_path)
    assert len(results) == 1
    assert results[0].rule_id == "ATL999"
    assert results[0].severity == Severity.ERROR


def test_engine_select_filter(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname = "test-pkg"\n')

    engine = LintEngine()
    engine.register(
        "ATL001",
        name="r1",
        description="Rule 1",
        severity=Severity.ERROR,
        layer="test",
        checker=_dummy_checker_fail,
    )
    engine.register(
        "ATL002",
        name="r2",
        description="Rule 2",
        severity=Severity.WARNING,
        layer="test",
        checker=_dummy_checker_fail,
    )

    # Select only ATL001
    results = engine.check(tmp_path, select=["ATL001"])
    assert len(results) == 1
    assert results[0].rule_id == "ATL999"  # dummy always returns ATL999


def test_engine_ignore_filter(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname = "test-pkg"\n')

    engine = LintEngine()
    engine.register(
        "ATL001",
        name="r1",
        description="Rule 1",
        severity=Severity.ERROR,
        layer="test",
        checker=_dummy_checker_fail,
    )

    results = engine.check(tmp_path, ignore=["ATL001"])
    assert results == []


def test_engine_summary() -> None:
    engine = LintEngine()
    results = [
        LintResult(rule_id="ATL001", severity=Severity.ERROR, message="err"),
        LintResult(rule_id="ATL002", severity=Severity.WARNING, message="warn"),
        LintResult(rule_id="ATL003", severity=Severity.ERROR, message="err2"),
    ]
    summary = engine.check_summary(results)
    assert summary == {"total": 3, "errors": 2, "warnings": 1}


def test_format_text_empty() -> None:
    assert format_text([]) == "No issues found."


def test_format_text_with_results() -> None:
    results = [
        LintResult(
            rule_id="ATL101",
            severity=Severity.ERROR,
            message="Hard import in core",
            file="my_tool/core/engine.py",
            line=3,
        ),
    ]
    output = format_text(results)
    assert "ATL101" in output
    assert "error" in output
    assert "1 issue found" in output


def test_format_json_structure() -> None:
    import json

    results = [
        LintResult(rule_id="ATL001", severity=Severity.ERROR, message="test"),
    ]
    output = format_json(results)
    data = json.loads(output)
    assert data["total"] == 1
    assert data["errors"] == 1
    assert len(data["issues"]) == 1


def test_lint_result_to_dict() -> None:
    r = LintResult(
        rule_id="ATL001",
        severity=Severity.ERROR,
        message="test",
        file="foo.py",
        line=10,
        hint="Fix it",
    )
    d = r.to_dict()
    assert d["rule_id"] == "ATL001"
    assert d["severity"] == "error"
    assert d["hint"] == "Fix it"


def test_register_method() -> None:
    engine = LintEngine()
    engine.register(
        "ATL888",
        name="reg-test",
        description="Test register",
        severity=Severity.WARNING,
        layer="test",
        checker=_dummy_checker_pass,
    )
    assert "ATL888" in engine.rules
    assert engine.rules["ATL888"].name == "reg-test"
