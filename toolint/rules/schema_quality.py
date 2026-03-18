"""Layer 5: Tool schema quality rules (ATL501–ATL504).

These rules ensure that the facade and MCP tools provide enough information
for LLMs to understand and correctly invoke them.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from toolint.core.ast_utils import detect_facade_class, find_classes, parse_file
from toolint.core.models import LintConfig, LintResult, Severity
from toolint.rules.registry import register


def _find_facade_file_and_class(
    pkg_dir: Path, facade_name: str
) -> tuple[Path | None, dict[str, Any] | None]:
    """Find the file and class info for the facade."""
    for py_file in pkg_dir.rglob("*.py"):
        tree = parse_file(py_file)
        if tree is None:
            continue
        for cls in find_classes(tree):
            if cls["name"] == facade_name:
                return py_file, cls
    return None, None


def _find_mcp_tool_functions(pkg_dir: Path) -> list[dict[str, Any]]:
    """Find functions decorated with @mcp_app.tool() or @server.tool() etc.

    Returns list of dicts: {name, file, line, has_docstring, docstring, args, returns}
    """
    mcp_files = [
        pkg_dir / "mcp_server.py",
        pkg_dir / "mcp_proxy.py",
        pkg_dir / "mcp.py",
        pkg_dir / "server.py",
    ]

    tools: list[dict[str, Any]] = []

    for mcp_file in mcp_files:
        if not mcp_file.exists():
            continue

        tree = parse_file(mcp_file)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # Check for @xxx.tool() decorator (user-facing tools only)
            # Skip MCP protocol handlers like @server.list_tools(),
            # @server.call_tool() — these are protocol-level, not user tools.
            is_tool = False
            for dec in node.decorator_list:
                dec_str = ast.dump(dec)
                dec_lower = dec_str.lower()
                # Skip protocol handlers
                if "list_tools" in dec_lower or "call_tool" in dec_lower:
                    continue
                if "tool" in dec_lower:
                    is_tool = True
                    break

            if not is_tool:
                continue

            # Extract docstring
            docstring = ""
            has_docstring = False
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring = node.body[0].value.value
                has_docstring = True

            # Extract args (skip self)
            args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]

            tools.append(
                {
                    "name": node.name,
                    "file": mcp_file,
                    "line": node.lineno,
                    "has_docstring": has_docstring,
                    "docstring": docstring,
                    "args": args,
                    "returns": node.returns is not None,
                    "return_annotation": (ast.dump(node.returns) if node.returns else None),
                }
            )

    return tools


@register(
    "ATL501",
    name="facade-docstrings",
    description="Facade public methods must have docstrings",
    severity=Severity.WARNING,
    layer="schema-quality",
)
def check_facade_docstrings(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that facade class public methods have docstrings."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    facade_name = detect_facade_class(pkg_dir, config.facade_class)
    if not facade_name:
        return []

    facade_file, facade_info = _find_facade_file_and_class(pkg_dir, facade_name)
    if not facade_file or not facade_info:
        return []

    results: list[LintResult] = []
    rel_path = facade_file.relative_to(project_dir)

    for method in facade_info["methods"]:
        if method["name"].startswith("_"):
            continue
        if not method["has_docstring"]:
            results.append(
                LintResult(
                    rule_id="ATL501",
                    severity=Severity.WARNING,
                    message=(f"Facade method '{facade_name}.{method['name']}()' has no docstring."),
                    file=str(rel_path),
                    line=method["line"],
                    hint=(
                        "Add a docstring describing what this method does. "
                        "LLMs and users rely on this for tool selection."
                    ),
                )
            )

    return results


@register(
    "ATL502",
    name="facade-type-hints",
    description="Facade public methods must have parameter + return type hints",
    severity=Severity.WARNING,
    layer="schema-quality",
)
def check_facade_type_hints(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that facade class public methods have type annotations."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    facade_name = detect_facade_class(pkg_dir, config.facade_class)
    if not facade_name:
        return []

    facade_file, facade_info = _find_facade_file_and_class(pkg_dir, facade_name)
    if not facade_file or not facade_info:
        return []

    # Re-parse to get full AST for type hint checking
    tree = parse_file(facade_file)
    if tree is None:
        return []

    results: list[LintResult] = []
    rel_path = facade_file.relative_to(project_dir)

    # Find the facade class node in AST
    facade_node: ast.ClassDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == facade_name:
            facade_node = node
            break

    if not facade_node:
        return []

    for item in facade_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name.startswith("_"):
            continue

        # Check return annotation
        if item.returns is None:
            results.append(
                LintResult(
                    rule_id="ATL502",
                    severity=Severity.WARNING,
                    message=(
                        f"Facade method '{facade_name}.{item.name}()' has no return type hint."
                    ),
                    file=str(rel_path),
                    line=item.lineno,
                    hint="Add a return type annotation (e.g. -> list[ToolSchema]).",
                )
            )

        # Check parameter annotations (skip self)
        for arg in item.args.args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                results.append(
                    LintResult(
                        rule_id="ATL502",
                        severity=Severity.WARNING,
                        message=(
                            f"Facade method '{facade_name}.{item.name}()' "
                            f"parameter '{arg.arg}' has no type hint."
                        ),
                        file=str(rel_path),
                        line=item.lineno,
                        hint="Add type annotations for all parameters.",
                    )
                )
                break  # One warning per method is enough

    return results


@register(
    "ATL503",
    name="mcp-tool-docstring",
    description="MCP tool functions must have docstrings (min 10 chars)",
    severity=Severity.ERROR,
    layer="schema-quality",
)
def check_mcp_tool_docstrings(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that MCP tool functions have meaningful docstrings."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    tools = _find_mcp_tool_functions(pkg_dir)
    if not tools:
        return []

    results: list[LintResult] = []

    for tool in tools:
        rel_path = tool["file"].relative_to(project_dir)

        if not tool["has_docstring"]:
            results.append(
                LintResult(
                    rule_id="ATL503",
                    severity=Severity.ERROR,
                    message=(
                        f"MCP tool '{tool['name']}' has no docstring. "
                        "LLMs rely on tool descriptions to select the right tool."
                    ),
                    file=str(rel_path),
                    line=tool["line"],
                    hint="Add a docstring describing what this tool does.",
                )
            )
        elif len(tool["docstring"].strip()) < 10:
            results.append(
                LintResult(
                    rule_id="ATL503",
                    severity=Severity.ERROR,
                    message=(
                        f"MCP tool '{tool['name']}' docstring is too short "
                        f"({len(tool['docstring'].strip())} chars, min 10). "
                        "LLMs need descriptive tool descriptions."
                    ),
                    file=str(rel_path),
                    line=tool["line"],
                    hint="Write a clear description of what this tool does and when to use it.",
                )
            )

    return results


@register(
    "ATL504",
    name="mcp-tool-param-docs",
    description="MCP tool function docstrings should describe each parameter",
    severity=Severity.WARNING,
    layer="schema-quality",
)
def check_mcp_tool_param_docs(
    project_dir: Path, config: LintConfig, pyproject: dict[str, Any]
) -> list[LintResult]:
    """Check that MCP tool docstrings document their parameters."""
    pkg_dir = project_dir / config.package
    if not pkg_dir.is_dir():
        return []

    tools = _find_mcp_tool_functions(pkg_dir)
    if not tools:
        return []

    results: list[LintResult] = []

    for tool in tools:
        if not tool["has_docstring"]:
            continue  # ATL503 already catches this

        args = tool["args"]
        if not args:
            continue

        docstring = tool["docstring"]

        # Check if docstring mentions parameters
        # Common patterns: "Args:", "Parameters:", or just mentioning param names
        has_param_section = any(
            marker in docstring for marker in ("Args:", "Parameters:", "Params:", ":param ")
        )

        if not has_param_section:
            # Check if at least param names are mentioned
            undocumented = [a for a in args if a not in docstring]
            if undocumented:
                rel_path = tool["file"].relative_to(project_dir)
                results.append(
                    LintResult(
                        rule_id="ATL504",
                        severity=Severity.WARNING,
                        message=(
                            f"MCP tool '{tool['name']}' docstring doesn't describe "
                            f"parameters: {', '.join(undocumented)}"
                        ),
                        file=str(rel_path),
                        line=tool["line"],
                        hint="Add an Args: section documenting each parameter.",
                    )
                )

    return results
