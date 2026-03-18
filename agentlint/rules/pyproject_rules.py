"""Layer 4: pyproject.toml consistency rules (ATL301–ATL303)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentlint.core.models import LintConfig, LintResult, Severity
from agentlint.rules.registry import register


def _get_scripts(pyproject: dict[str, Any]) -> dict[str, str]:
    """Get CLI scripts from pyproject.toml (Poetry or PEP 621)."""
    # Poetry style
    scripts = pyproject.get("tool", {}).get("poetry", {}).get("scripts", {})
    if scripts:
        return scripts

    # PEP 621 style
    return pyproject.get("project", {}).get("scripts", {})


def _get_extras(pyproject: dict[str, Any]) -> dict[str, list[str]]:
    """Get extras groups from pyproject.toml."""
    # Poetry style
    extras = pyproject.get("tool", {}).get("poetry", {}).get("extras", {})
    if extras:
        return extras

    # PEP 621 style
    return pyproject.get("project", {}).get("optional-dependencies", {})


def _normalize(name: str) -> str:
    """Normalize package name for comparison."""
    return name.lower().replace("-", "_").strip()


@register(
    "ATL301",
    name="scripts-entry",
    description="CLI entry point must be registered in pyproject.toml scripts",
    severity=Severity.ERROR,
    layer="pyproject",
)
def check_scripts_entry(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that a CLI entry point is registered."""
    pkg_dir = project_dir / config.package
    main_file = pkg_dir / "__main__.py"

    if not main_file.exists():
        return []  # ATL002 already catches missing __main__.py

    scripts = _get_scripts(pyproject)
    if not scripts:
        return [
            LintResult(
                rule_id="ATL301",
                severity=Severity.ERROR,
                message="No CLI scripts registered in pyproject.toml.",
                file="pyproject.toml",
                hint=(
                    "Add [tool.poetry.scripts] or [project.scripts] "
                    "with an entry pointing to __main__:main"
                ),
            )
        ]

    # Check that at least one script points to the package
    for _name, entry in scripts.items():
        if config.package in entry:
            return []

    return [
        LintResult(
            rule_id="ATL301",
            severity=Severity.ERROR,
            message=f"No script entry points to '{config.package}' in pyproject.toml.",
            file="pyproject.toml",
            hint=f'Add: my-cli = "{config.package}.__main__:main"',
        )
    ]


@register(
    "ATL302",
    name="mcp-extras",
    description="If MCP server exists, mcp extras group must be defined",
    severity=Severity.ERROR,
    layer="pyproject",
)
def check_mcp_extras(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that mcp extras exists if mcp_server.py is present."""
    pkg_dir = project_dir / config.package

    # Check if any MCP server file exists
    mcp_files = [
        pkg_dir / "mcp_server.py",
        pkg_dir / "mcp.py",
        pkg_dir / "server.py",
    ]
    has_mcp = any(f.exists() for f in mcp_files)

    if not has_mcp:
        return []

    extras = _get_extras(pyproject)
    if "mcp" not in extras:
        return [
            LintResult(
                rule_id="ATL302",
                severity=Severity.ERROR,
                message="MCP server file found but no 'mcp' extras group in pyproject.toml.",
                file="pyproject.toml",
                hint='Add: mcp = ["mcp"] to extras.',
            )
        ]

    return []


@register(
    "ATL303",
    name="all-extras-complete",
    description="all extras group must include all deps from other extras groups",
    severity=Severity.WARNING,
    layer="pyproject",
)
def check_all_extras_complete(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that 'all' extras includes everything from other groups."""
    extras = _get_extras(pyproject)

    if "all" not in extras:
        return []  # no 'all' group is fine — not required

    all_deps = {_normalize(d) for d in extras["all"]}

    missing: list[str] = []
    for group, deps in extras.items():
        if group == "all":
            continue
        for dep in deps:
            if _normalize(dep) not in all_deps:
                missing.append(f"{dep} (from '{group}')")

    if missing:
        return [
            LintResult(
                rule_id="ATL303",
                severity=Severity.WARNING,
                message=f"'all' extras missing: {', '.join(missing)}",
                file="pyproject.toml",
                hint="Add missing dependencies to the 'all' extras group.",
            )
        ]

    return []
