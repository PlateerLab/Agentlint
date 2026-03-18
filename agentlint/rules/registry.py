"""Global rule registry — rules register themselves on import."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentlint.core.models import LintConfig, LintResult, RuleDefinition, Severity

RuleChecker = Callable[[Path, LintConfig, dict[str, Any]], list[LintResult]]

_REGISTRY: list[tuple[str, RuleDefinition, RuleChecker]] = []


def register(
    rule_id: str,
    *,
    name: str,
    description: str,
    severity: Severity,
    layer: str,
) -> Callable[[RuleChecker], RuleChecker]:
    """Decorator to register a rule checker into the global registry."""

    def decorator(fn: RuleChecker) -> RuleChecker:
        rule_def = RuleDefinition(
            id=rule_id,
            name=name,
            description=description,
            severity=severity,
            layer=layer,
        )
        _REGISTRY.append((rule_id, rule_def, fn))
        return fn

    return decorator


def get_all() -> list[tuple[str, RuleDefinition, RuleChecker]]:
    """Return all registered rules."""
    # Trigger import of rule modules so they register themselves
    from agentlint.rules import (  # noqa: F401
        dependency,
        layer_separation,
        pyproject_rules,
        schema_quality,
        structure,
    )

    return list(_REGISTRY)
