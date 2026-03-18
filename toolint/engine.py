"""LintEngine — rule registry, runner, and result collector."""

from __future__ import annotations

from pathlib import Path

from toolint.core.config import load_config
from toolint.core.models import LintResult, RuleDefinition, Severity
from toolint.rules.registry import RuleChecker


class LintEngine:
    """Main facade: registers rules, runs checks, collects results."""

    def __init__(self) -> None:
        self._rules: dict[str, RuleDefinition] = {}
        self._checkers: dict[str, RuleChecker] = {}

    def register(
        self,
        rule_id: str,
        *,
        name: str,
        description: str,
        severity: Severity,
        layer: str,
        checker: RuleChecker,
    ) -> None:
        """Register a lint rule with its checker function."""
        self._rules[rule_id] = RuleDefinition(
            id=rule_id,
            name=name,
            description=description,
            severity=severity,
            layer=layer,
        )
        self._checkers[rule_id] = checker

    @property
    def rules(self) -> dict[str, RuleDefinition]:
        return dict(self._rules)

    def check(
        self,
        project_dir: str | Path,
        *,
        select: list[str] | None = None,
        ignore: list[str] | None = None,
    ) -> list[LintResult]:
        """Run all applicable rules against a project directory.

        Parameters
        ----------
        project_dir:
            Path to the project root (where pyproject.toml lives).
        select:
            If provided, only run these rule IDs. Overrides config.
        ignore:
            If provided, skip these rule IDs. Merged with config.
        """
        project_path = Path(project_dir).resolve()
        config, pyproject = load_config(project_path)

        # Determine which rules to run
        effective_select = select or config.select
        effective_ignore = set(ignore or []) | set(config.ignore)

        rule_ids = list(self._checkers.keys())
        if effective_select:
            rule_ids = [r for r in rule_ids if r in effective_select]
        rule_ids = [r for r in rule_ids if r not in effective_ignore]

        # Run checkers
        results: list[LintResult] = []
        for rule_id in sorted(rule_ids):
            checker = self._checkers[rule_id]
            try:
                issues = checker(project_path, config, pyproject)
                results.extend(issues)
            except Exception as exc:
                results.append(
                    LintResult(
                        rule_id=rule_id,
                        severity=Severity.WARNING,
                        message=f"Rule {rule_id} crashed: {exc}",
                    )
                )

        return results

    def check_summary(self, results: list[LintResult]) -> dict[str, int]:
        """Return error/warning counts from results."""
        errors = sum(1 for r in results if r.severity == Severity.ERROR)
        warnings = sum(1 for r in results if r.severity == Severity.WARNING)
        return {"total": len(results), "errors": errors, "warnings": warnings}
