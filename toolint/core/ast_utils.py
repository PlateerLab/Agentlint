"""AST parsing utilities — shared helpers for rule checkers."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any


def parse_file(path: Path) -> ast.Module | None:
    """Parse a Python file and return the AST, or None on failure."""
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def get_imports(tree: ast.Module) -> list[dict[str, Any]]:
    """Extract all import statements from an AST.

    Returns list of dicts with keys:
        - module: str (e.g. "numpy", "graph_tool_call.core.tool")
        - names: list[str] (imported names, or ["*"])
        - line: int
        - col: int
        - in_try_except: bool (inside try/except ImportError)
        - except_body: str (the except block source, for hint detection)
    """
    results: list[dict[str, Any]] = []

    # First, collect line ranges covered by try/except ImportError blocks
    try_except_ranges: list[tuple[int, int, str]] = []  # (start, end, except_body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if _is_import_error_handler(handler):
                    start = node.lineno
                    end = max(
                        (
                            getattr(n, "end_lineno", 0) or getattr(n, "lineno", 0)
                            for n in ast.walk(node)
                            if hasattr(n, "lineno")
                        ),
                        default=node.lineno,
                    )
                    except_body = ast.dump(handler)
                    try_except_ranges.append((start, end, except_body))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                in_try, body = _in_try_except(node.lineno, try_except_ranges)
                results.append(
                    {
                        "module": alias.name,
                        "top_module": top_module,
                        "names": [alias.asname or alias.name],
                        "line": node.lineno,
                        "col": node.col_offset,
                        "in_try_except": in_try,
                        "except_body": body,
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                names = [a.name for a in node.names]
                in_try, body = _in_try_except(node.lineno, try_except_ranges)
                results.append(
                    {
                        "module": node.module,
                        "top_module": top_module,
                        "names": names,
                        "line": node.lineno,
                        "col": node.col_offset,
                        "in_try_except": in_try,
                        "except_body": body,
                    }
                )

    return results


def _is_import_error_handler(handler: ast.ExceptHandler) -> bool:
    """Check if an except handler catches ImportError (or ModuleNotFoundError)."""
    if handler.type is None:
        return True  # bare except
    if isinstance(handler.type, ast.Name):
        return handler.type.id in ("ImportError", "ModuleNotFoundError")
    if isinstance(handler.type, ast.Tuple):
        for elt in handler.type.elts:
            if isinstance(elt, ast.Name) and elt.id in ("ImportError", "ModuleNotFoundError"):
                return True
    return False


def _in_try_except(lineno: int, ranges: list[tuple[int, int, str]]) -> tuple[bool, str]:
    """Check if a line number falls within any try/except ImportError block."""
    for start, end, body in ranges:
        if start <= lineno <= end:
            return True, body
    return False, ""


def find_classes(tree: ast.Module) -> list[dict[str, Any]]:
    """Find top-level class definitions.

    Returns list of dicts with keys: name, line, bases, has_docstring, methods
    """
    classes: list[dict[str, Any]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        {
                            "name": item.name,
                            "line": item.lineno,
                            "has_docstring": _has_docstring(item),
                            "args": [a.arg for a in item.args.args if a.arg != "self"],
                            "returns": item.returns is not None,
                        }
                    )
            classes.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "bases": [_base_name(b) for b in node.bases],
                    "has_docstring": _has_docstring(node),
                    "methods": methods,
                    "method_count": len([m for m in methods if not m["name"].startswith("_")]),
                }
            )
    return classes


def find_assignments(tree: ast.Module, name: str) -> list[dict[str, Any]]:
    """Find top-level assignments to a specific name (e.g. __version__, __all__)."""
    results: list[dict[str, Any]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    results.append(
                        {
                            "line": node.lineno,
                            "value": _eval_constant(node.value),
                        }
                    )
    return results


def _has_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    """Check if a function/class has a docstring."""
    if node.body and isinstance(node.body[0], ast.Expr):
        val = node.body[0].value
        return isinstance(val, ast.Constant) and isinstance(val.value, str)
    return False


def _base_name(node: ast.expr) -> str:
    """Get the name of a base class node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_base_name(node.value)}.{node.attr}"
    return ""


def _eval_constant(node: ast.expr) -> Any:
    """Try to evaluate a constant expression (string, list of strings, etc.)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval_constant(elt) for elt in node.elts]
    return None


# --- stdlib detection ---

_STDLIB_MODULES: frozenset[str] | None = None


def stdlib_module_names() -> frozenset[str]:
    """Return the set of stdlib top-level module names."""
    global _STDLIB_MODULES
    if _STDLIB_MODULES is not None:
        return _STDLIB_MODULES

    _STDLIB_MODULES = frozenset(sys.stdlib_module_names)

    return _STDLIB_MODULES


def is_stdlib(module_name: str) -> bool:
    """Check if a top-level module name belongs to stdlib."""
    return module_name in stdlib_module_names() or module_name.startswith("_")


def is_internal(module_name: str, package: str) -> bool:
    """Check if a module is internal to the package."""
    return module_name == package or module_name.startswith(f"{package}.")


# --- facade detection ---

MIN_FACADE_METHODS = 3
"""Minimum number of public methods for a class to be considered a facade."""


def detect_facade_class(pkg_dir: Path, config_facade_class: str = "") -> str | None:
    """Detect the facade class name from the package.

    If ``config_facade_class`` is set, returns it directly.
    Otherwise auto-detects the class with the most public methods
    (outside core/, tests/, __init__.py).
    """
    if config_facade_class:
        return config_facade_class

    best: tuple[str, int] | None = None
    for py_file in pkg_dir.rglob("*.py"):
        rel = py_file.relative_to(pkg_dir)
        parts = rel.parts
        if any(p in ("core", "tests", "__pycache__") for p in parts):
            continue
        if py_file.name == "__init__.py":
            continue

        tree = parse_file(py_file)
        if tree is None:
            continue
        for cls in find_classes(tree):
            count = cls["method_count"]
            if count >= MIN_FACADE_METHODS and (best is None or count > best[1]):
                best = (cls["name"], count)

    return best[0] if best else None


def is_lazy_import(tree: ast.Module, lineno: int) -> bool:
    """Check if an import at the given line is inside a function/method body."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= lineno <= end_line:
                return True
    return False


def is_graceful_fallback(tree: ast.Module, import_line: int) -> bool:
    """Check if an import guard's except block is a graceful fallback.

    Graceful fallbacks (= None, = False, return, pass) don't need install hints
    because the feature silently degrades without bothering the user.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue

        try_start = node.lineno
        try_end = max(
            (
                getattr(n, "end_lineno", 0) or getattr(n, "lineno", 0)
                for n in ast.walk(node)
                if hasattr(n, "lineno")
            ),
            default=node.lineno,
        )
        if not (try_start <= import_line <= try_end):
            continue

        for handler in node.handlers:
            if handler.type is None:
                continue
            type_name = ""
            if isinstance(handler.type, ast.Name):
                type_name = handler.type.id
            if type_name not in ("ImportError", "ModuleNotFoundError"):
                continue

            for stmt in handler.body:
                if isinstance(stmt, ast.Assign):
                    if isinstance(stmt.value, ast.Constant) and stmt.value.value in (
                        None,
                        False,
                    ):
                        return True
                if isinstance(stmt, ast.Return):
                    return True
                if isinstance(stmt, ast.Pass):
                    return True

    return False
