"""Layer 2: Dependency rules (ATL101–ATL105)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentlint.core.ast_utils import get_imports, is_internal, is_stdlib, parse_file
from agentlint.core.models import LintConfig, LintResult, Severity
from agentlint.rules.registry import register


def _get_extras_packages(pyproject: dict[str, Any]) -> dict[str, list[str]]:
    """Extract extras group -> package names from pyproject.toml.

    Returns {group_name: [package_name, ...]} where package_name is normalized
    to the importable form (e.g. "sentence-transformers" -> "sentence_transformers").
    """
    extras: dict[str, list[str]] = {}

    # Poetry style
    poetry_extras = pyproject.get("tool", {}).get("poetry", {}).get("extras", {})
    if poetry_extras:
        for group, deps in poetry_extras.items():
            extras[group] = [_normalize_package_name(d) for d in deps]
        return extras

    # PEP 621 style
    project_extras = pyproject.get("project", {}).get("optional-dependencies", {})
    if project_extras:
        for group, deps in project_extras.items():
            # PEP 621 deps can have version specifiers
            extras[group] = [
                _normalize_package_name(
                    d.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
                )
                for d in deps
            ]

    return extras


def _normalize_package_name(name: str) -> str:
    """Normalize package name to importable module name."""
    return name.lower().replace("-", "_").strip()


def _get_all_extras_packages(pyproject: dict[str, Any]) -> set[str]:
    """Get all package names across all extras groups (except 'all')."""
    extras = _get_extras_packages(pyproject)
    all_pkgs: set[str] = set()
    for group, pkgs in extras.items():
        if group != "all":
            all_pkgs.update(pkgs)
    return all_pkgs


def _get_required_deps(pyproject: dict[str, Any]) -> set[str]:
    """Get required (non-optional) dependency names."""
    deps: set[str] = set()

    # Poetry style
    poetry_deps = pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, spec in poetry_deps.items():
        if name == "python":
            continue
        if isinstance(spec, dict) and spec.get("optional", False):
            continue
        deps.add(_normalize_package_name(name))

    # PEP 621 style
    project_deps = pyproject.get("project", {}).get("dependencies", [])
    for dep in project_deps:
        name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        deps.add(_normalize_package_name(name))

    return deps


@register(
    "ATL101",
    name="core-stdlib-only",
    description="No third-party imports in core/ directory (stdlib only)",
    severity=Severity.ERROR,
    layer="dependency",
)
def check_core_stdlib_only(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that core/ only imports stdlib and internal modules."""
    pkg_dir = project_dir / config.package
    core_dir = pkg_dir / config.core_dir

    if not core_dir.is_dir():
        return []

    allowed = set(config.core_allowed_imports)
    results: list[LintResult] = []

    for py_file in core_dir.rglob("*.py"):
        tree = parse_file(py_file)
        if tree is None:
            continue

        for imp in get_imports(tree):
            top = imp["top_module"]
            if is_stdlib(top):
                continue
            if is_internal(top, config.package):
                continue
            if top in allowed:
                continue

            rel_path = py_file.relative_to(project_dir)
            results.append(
                LintResult(
                    rule_id="ATL101",
                    severity=Severity.ERROR,
                    message=(
                        f"Hard import of '{imp['module']}' in core module"
                        " — core must be stdlib-only."
                    ),
                    file=str(rel_path),
                    line=imp["line"],
                    col=imp["col"],
                    hint="Use try/except ImportError guard or move to a non-core module.",
                )
            )

    return results


@register(
    "ATL102",
    name="optional-import-guard",
    description="Optional dependencies must use try/except ImportError guard",
    severity=Severity.ERROR,
    layer="dependency",
)
def check_optional_import_guard(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that optional deps are imported inside try/except ImportError."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    optional_pkgs = _get_all_extras_packages(pyproject)
    if not optional_pkgs:
        return []

    results: list[LintResult] = []

    for py_file in pkg_dir.rglob("*.py"):
        tree = parse_file(py_file)
        if tree is None:
            continue

        for imp in get_imports(tree):
            top = imp["top_module"]
            if top not in optional_pkgs:
                continue
            if imp["in_try_except"]:
                continue
            # Skip if it's inside a function/method (lazy import)
            if _is_lazy_import(py_file, imp["line"]):
                continue

            rel_path = py_file.relative_to(project_dir)
            results.append(
                LintResult(
                    rule_id="ATL102",
                    severity=Severity.ERROR,
                    message=(
                        f"Optional import '{imp['module']}' missing try/except ImportError guard."
                    ),
                    file=str(rel_path),
                    line=imp["line"],
                    col=imp["col"],
                )
            )

    return results


def _is_lazy_import(py_file: Path, lineno: int) -> bool:
    """Check if an import at the given line is inside a function/method body (lazy import)."""
    import ast

    tree = parse_file(py_file)
    if tree is None:
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= lineno <= end_line:
                return True
    return False


@register(
    "ATL103",
    name="import-guard-hint",
    description="Import guard must include install hint (e.g. pip install pkg[extra])",
    severity=Severity.WARNING,
    layer="dependency",
)
def check_import_guard_hint(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that import guards include an install hint in the except block."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    optional_pkgs = _get_all_extras_packages(pyproject)
    if not optional_pkgs:
        return []

    results: list[LintResult] = []

    for py_file in pkg_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = parse_file(py_file)
        if tree is None:
            continue

        for imp in get_imports(tree):
            top = imp["top_module"]
            if top not in optional_pkgs:
                continue
            if not imp["in_try_except"]:
                continue

            # Check if the except block or nearby code mentions "pip install" or "install"
            # Look at lines around the import for install hints
            lines = source.splitlines()
            start = max(0, imp["line"] - 1)
            end = min(len(lines), imp["line"] + 15)
            block = "\n".join(lines[start:end])

            if "pip install" not in block and "install" not in block.lower():
                rel_path = py_file.relative_to(project_dir)
                results.append(
                    LintResult(
                        rule_id="ATL103",
                        severity=Severity.WARNING,
                        message=f"Import guard for '{imp['module']}' has no install hint.",
                        file=str(rel_path),
                        line=imp["line"],
                        hint=f"Add a message like: pip install {config.package}[<extra>]",
                    )
                )

    return results


@register(
    "ATL104",
    name="extras-registered",
    description="Optional imports must be registered in pyproject.toml extras",
    severity=Severity.ERROR,
    layer="dependency",
)
def check_extras_registered(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that try/except guarded imports are in pyproject.toml extras."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    optional_pkgs = _get_all_extras_packages(pyproject)
    required_pkgs = _get_required_deps(pyproject)
    results: list[LintResult] = []
    seen: set[str] = set()

    for py_file in pkg_dir.rglob("*.py"):
        tree = parse_file(py_file)
        if tree is None:
            continue

        for imp in get_imports(tree):
            if not imp["in_try_except"]:
                continue
            top = imp["top_module"]
            if is_stdlib(top):
                continue
            if is_internal(top, config.package):
                continue
            if top in optional_pkgs or top in required_pkgs:
                continue
            if top in seen:
                continue
            seen.add(top)

            rel_path = py_file.relative_to(project_dir)
            results.append(
                LintResult(
                    rule_id="ATL104",
                    severity=Severity.ERROR,
                    message=(
                        f"Optional import '{top}' is guarded but not registered "
                        f"in pyproject.toml extras."
                    ),
                    file=str(rel_path),
                    line=imp["line"],
                    hint="Add it to an extras group in pyproject.toml.",
                )
            )

    return results


@register(
    "ATL105",
    name="init-no-eager-optional",
    description="__init__.py should not eagerly import optional-dep modules",
    severity=Severity.WARNING,
    layer="dependency",
)
def check_init_no_eager_optional(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check __init__.py doesn't eagerly import modules that use optional deps."""
    pkg_dir = project_dir / config.package
    init_file = pkg_dir / "__init__.py"

    if not init_file.exists():
        return []

    optional_pkgs = _get_all_extras_packages(pyproject)
    if not optional_pkgs:
        return []

    tree = parse_file(init_file)
    if tree is None:
        return []

    results: list[LintResult] = []

    for imp in get_imports(tree):
        top = imp["top_module"]
        # Direct import of optional package at top level of __init__.py
        if top in optional_pkgs and not imp["in_try_except"]:
            if not _is_lazy_import(init_file, imp["line"]):
                rel_path = init_file.relative_to(project_dir)
                results.append(
                    LintResult(
                        rule_id="ATL105",
                        severity=Severity.WARNING,
                        message=f"__init__.py eagerly imports optional dep '{imp['module']}'.",
                        file=str(rel_path),
                        line=imp["line"],
                        hint="Use lazy imports (__getattr__) or move to a submodule.",
                    )
                )

    return results
