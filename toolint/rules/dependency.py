"""Layer 2: Dependency rules (ATL101–ATL105)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from toolint.core.ast_utils import (
    get_imports,
    is_graceful_fallback,
    is_internal,
    is_lazy_import,
    is_stdlib,
    parse_file,
)
from toolint.core.models import LintConfig, LintResult, Severity
from toolint.rules.registry import register

# Well-known package-name → import-name mappings.
# When the PyPI package name differs from the Python import name.
_PACKAGE_TO_IMPORT: dict[str, str] = {
    "pyyaml": "yaml",
    "pillow": "PIL",
    "scikit-learn": "sklearn",
    "python-dateutil": "dateutil",
    "beautifulsoup4": "bs4",
    "opencv-python": "cv2",
    "pymongo": "pymongo",
}

# Reverse: import-name → package-name(s)
_IMPORT_TO_PACKAGE: dict[str, str] = {v.lower(): k for k, v in _PACKAGE_TO_IMPORT.items()}


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
    """Get all package names across all extras groups (except 'all').

    Includes both the normalized package name AND the known import name,
    so that e.g. "pyyaml" matches imports of "yaml".
    """
    extras = _get_extras_packages(pyproject)
    all_pkgs: set[str] = set()
    for group, pkgs in extras.items():
        if group == "all":
            continue
        for pkg in pkgs:
            all_pkgs.add(pkg)
            # Add known import-name alias
            if pkg in _PACKAGE_TO_IMPORT:
                all_pkgs.add(_PACKAGE_TO_IMPORT[pkg].lower())
    return all_pkgs


def _get_all_extras_raw_packages(pyproject: dict[str, Any]) -> set[str]:
    """Get raw package names from extras (for ATL104 matching).

    Returns both normalized names and their known import aliases.
    """
    return _get_all_extras_packages(pyproject)


def _get_required_deps(pyproject: dict[str, Any]) -> set[str]:
    """Get required (non-optional) dependency names.

    Includes both normalized package names and known import aliases.
    """
    deps: set[str] = set()

    # Poetry style
    poetry_deps = pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, spec in poetry_deps.items():
        if name == "python":
            continue
        if isinstance(spec, dict) and spec.get("optional", False):
            continue
        normalized = _normalize_package_name(name)
        deps.add(normalized)
        if normalized in _PACKAGE_TO_IMPORT:
            deps.add(_PACKAGE_TO_IMPORT[normalized].lower())

    # PEP 621 style
    project_deps = pyproject.get("project", {}).get("dependencies", [])
    for dep in project_deps:
        name = dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()
        normalized = _normalize_package_name(name)
        deps.add(normalized)
        if normalized in _PACKAGE_TO_IMPORT:
            deps.add(_PACKAGE_TO_IMPORT[normalized].lower())

    return deps


def _has_require_function(tree: ast.Module, source: str) -> bool:
    """Check if the file has a _require_xxx() function with an install hint.

    Pattern: def _require_xxx(): ... raise ImportError("... install ...")
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("_require"):
            continue
        func_start = node.lineno
        func_end = getattr(node, "end_lineno", node.lineno)
        lines = source.splitlines()
        func_text = "\n".join(lines[func_start - 1 : func_end])
        if "install" in func_text.lower():
            return True

    return False


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
            hint = (
                "Move this module outside of core/, or add "
                f"'{top}' to core_allowed_imports in [tool.toolint]."
            )
            results.append(
                LintResult(
                    rule_id="ATL101",
                    severity=Severity.ERROR,
                    message=(
                        f"Third-party import '{imp['module']}' in core module"
                        " — core/ must be stdlib-only."
                    ),
                    file=str(rel_path),
                    line=imp["line"],
                    col=imp["col"],
                    hint=hint,
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
            if is_lazy_import(tree, imp["line"]):
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
    """Check that import guards include an install hint in the except block.

    Skips graceful fallbacks (= None, = False, return, pass) since those
    silently degrade without user interaction.
    """
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

            # Skip graceful fallbacks — they don't need install hints
            if is_graceful_fallback(tree, imp["line"]):
                continue

            # Check near the import (15 lines), the whole file for
            # _require_xxx() functions, or "install" mentions
            lines = source.splitlines()
            start = max(0, imp["line"] - 1)
            end = min(len(lines), imp["line"] + 15)
            block = "\n".join(lines[start:end])

            has_hint = (
                "pip install" in block
                or "install" in block.lower()
                or _has_require_function(tree, source)
            )

            if not has_hint:
                rel_path = py_file.relative_to(project_dir)
                results.append(
                    LintResult(
                        rule_id="ATL103",
                        severity=Severity.WARNING,
                        message=(
                            f"Import guard for '{imp['module']}' "
                            "raises an error but has no install hint."
                        ),
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
    """Check that try/except guarded imports are in pyproject.toml extras.

    Skips lazy imports (inside functions) since those are typically
    user-triggered and may not need extras registration.
    """
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    optional_pkgs = _get_all_extras_packages(pyproject)
    required_pkgs = _get_required_deps(pyproject)
    known_pkgs = optional_pkgs | required_pkgs
    results: list[LintResult] = []
    seen: set[str] = set()

    for py_file in pkg_dir.rglob("*.py"):
        tree = parse_file(py_file)
        if tree is None:
            continue

        for imp in get_imports(tree):
            if not imp["in_try_except"]:
                continue
            # Skip lazy imports inside functions
            if is_lazy_import(tree, imp["line"]):
                continue

            top = imp["top_module"]
            if is_stdlib(top):
                continue
            if is_internal(top, config.package):
                continue
            if top in known_pkgs:
                continue
            # Check reverse mapping: import "yaml" → package "pyyaml"
            if top.lower() in _IMPORT_TO_PACKAGE:
                mapped_pkg = _normalize_package_name(_IMPORT_TO_PACKAGE[top.lower()])
                if mapped_pkg in known_pkgs:
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
            if not is_lazy_import(tree, imp["line"]):
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
