"""Layer 3: Layer separation rules (ATL201–ATL203).

These rules enforce the facade pattern — interface layers (MCP server, CLI,
middleware) must go through the facade class, not call internal modules directly.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from toolint.core.ast_utils import detect_facade_class, find_classes, get_imports, parse_file
from toolint.core.models import LintConfig, LintResult, Severity
from toolint.rules.registry import register


def _get_interface_files(pkg_dir: Path, config: LintConfig) -> list[Path]:
    """Get interface file paths that exist in the package."""
    files: list[Path] = []
    for name in config.interface_files:
        path = pkg_dir / name
        if path.exists():
            files.append(path)
    return files


def _find_facade_module(pkg_dir: Path, facade_class: str) -> str | None:
    """Find which module defines the facade class. Returns module path like 'my_pkg.tool_graph'."""
    for py_file in pkg_dir.rglob("*.py"):
        tree = parse_file(py_file)
        if tree is None:
            continue
        for cls in find_classes(tree):
            if cls["name"] == facade_class:
                rel = py_file.relative_to(pkg_dir.parent)
                module = str(rel).replace("/", ".").removesuffix(".py")
                return module
    return None


def _is_type_or_constant_import(imp: dict[str, Any], pkg_dir: Path) -> bool:
    """Check if an import is likely a type/enum/constant (not business logic).

    Heuristics:
    - Module name contains 'schema', 'models', 'types', 'protocol', 'constants'
    - Imported names are ALL_CAPS (constants) or PascalCase (types/enums)
    """
    module = imp["module"]
    module_lower = module.lower()

    # Module-level hints
    type_module_keywords = ("schema", "models", "types", "protocol", "constants", "enums")
    if any(kw in module_lower for kw in type_module_keywords):
        return True

    # Check imported names
    names = imp.get("names", [])
    for name in names:
        # ALL_CAPS = constant
        if name.isupper() or (name.startswith("_") and name[1:].isupper()):
            return True
        # PascalCase = type/enum/class
        if name[0:1].isupper() and not name.isupper():
            return True

    return False


def _get_internal_imports(tree: ast.Module, package: str) -> list[dict[str, Any]]:
    """Get imports that reference internal package modules (not facade)."""
    imports = get_imports(tree)
    return [imp for imp in imports if imp["module"].startswith(f"{package}.")]


@register(
    "ATL201",
    name="interface-no-business-logic",
    description=(
        "Interface files should not call internal business logic directly "
        "(type/enum/constant imports are allowed)"
    ),
    severity=Severity.WARNING,
    layer="layer-separation",
)
def check_interface_no_business_logic(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that interface files import the facade, not internal modules."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    facade_class = detect_facade_class(pkg_dir, config.facade_class)
    if not facade_class:
        return []

    facade_module = _find_facade_module(pkg_dir, facade_class)
    interface_files = _get_interface_files(pkg_dir, config)

    if not interface_files:
        return []

    results: list[LintResult] = []

    for iface_file in interface_files:
        tree = parse_file(iface_file)
        if tree is None:
            continue

        internal_imports = _get_internal_imports(tree, config.package)

        for imp in internal_imports:
            module = imp["module"]

            # Allow imports from the facade module itself
            if facade_module and module == facade_module:
                continue

            # Allow imports from __init__ (public API)
            if module == config.package:
                continue

            # Allow type/enum/constant imports
            if _is_type_or_constant_import(imp, pkg_dir):
                continue

            # Allow interface files importing other interface files
            # (e.g. __main__.py importing mcp_server for cmd_serve)
            module_file = module.split(".")[-1]
            if any(module_file == iface.stem for iface in interface_files):
                continue

            rel_path = iface_file.relative_to(project_dir)
            iface_name = iface_file.name
            results.append(
                LintResult(
                    rule_id="ATL201",
                    severity=Severity.WARNING,
                    message=(
                        f"'{iface_name}' imports internal module '{module}' "
                        f"instead of using facade '{facade_class}'."
                    ),
                    file=str(rel_path),
                    line=imp["line"],
                    hint=(
                        f"Import from '{config.package}' or the facade module, "
                        "not internal submodules."
                    ),
                )
            )

    return results


@register(
    "ATL202",
    name="cli-uses-facade",
    description="CLI command handlers should invoke functionality through the facade class",
    severity=Severity.WARNING,
    layer="layer-separation",
)
def check_cli_uses_facade(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that CLI __main__.py uses the facade class."""
    pkg_dir = project_dir / config.package
    main_file = pkg_dir / "__main__.py"

    if not main_file.exists():
        return []

    facade_class = detect_facade_class(pkg_dir, config.facade_class)
    if not facade_class:
        return []

    tree = parse_file(main_file)
    if tree is None:
        return []

    source = main_file.read_text(encoding="utf-8")

    # Check: does __main__.py reference the facade class anywhere?
    if facade_class in source:
        return []

    # Also check if it imports from the package's public API
    for imp in get_imports(tree):
        if imp["module"] == config.package:
            # Importing from __init__ — check if facade is in the names
            if facade_class in imp.get("names", []):
                return []

    return [
        LintResult(
            rule_id="ATL202",
            severity=Severity.WARNING,
            message=(
                f"'__main__.py' does not reference facade class '{facade_class}'. "
                "CLI commands should go through the facade."
            ),
            file=str(main_file.relative_to(project_dir)),
            hint=f"Import and use '{facade_class}' in command handlers.",
        )
    ]


@register(
    "ATL203",
    name="interface-no-core-import",
    description=(
        "Interface layer should not import core/ internals directly (except types/constants)"
    ),
    severity=Severity.WARNING,
    layer="layer-separation",
)
def check_interface_no_core_import(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that interface files don't import from core/ directly."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    core_prefix = f"{config.package}.{config.core_dir}"
    interface_files = _get_interface_files(pkg_dir, config)

    if not interface_files:
        return []

    results: list[LintResult] = []

    for iface_file in interface_files:
        tree = parse_file(iface_file)
        if tree is None:
            continue

        for imp in get_imports(tree):
            module = imp["module"]
            if not module.startswith(core_prefix):
                continue

            # Allow type/enum/constant imports
            if _is_type_or_constant_import(imp, pkg_dir):
                continue

            rel_path = iface_file.relative_to(project_dir)
            results.append(
                LintResult(
                    rule_id="ATL203",
                    severity=Severity.WARNING,
                    message=(
                        f"'{iface_file.name}' imports from core module '{module}'. "
                        "Interface layer should use the facade, not core internals."
                    ),
                    file=str(rel_path),
                    line=imp["line"],
                    hint=(
                        "Import from the public API or facade instead. "
                        "Type/enum/constant imports are allowed."
                    ),
                )
            )

    return results
