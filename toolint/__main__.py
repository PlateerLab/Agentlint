"""CLI entry point: python -m toolint."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from toolint import __version__

if TYPE_CHECKING:
    from toolint.engine import LintEngine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toolint",
        description="Structural linter for MCP-compatible Python agent tool packages",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    # --- check ---
    p_check = sub.add_parser("check", help="Lint a project directory")
    p_check.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project directory (default: current directory)",
    )
    p_check.add_argument(
        "--select",
        help="Comma-separated rule IDs to run (e.g. ATL101,ATL102)",
    )
    p_check.add_argument(
        "--ignore",
        help="Comma-separated rule IDs to skip (e.g. ATL105)",
    )
    p_check.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        dest="output_format",
        help="Output format (default: text)",
    )

    # --- rules ---
    sub.add_parser("rules", help="List all available rules")

    return parser


def _get_engine() -> LintEngine:
    """Create a LintEngine with all rules registered."""
    from toolint.engine import LintEngine

    engine = LintEngine()

    # Import rule modules to trigger registration
    # (rules use @engine.rule decorator or register directly)
    # For now, rules register themselves into a global registry,
    # then we load them into the engine.
    from toolint.rules import registry

    for rule_id, rule_def, checker in registry.get_all():
        engine.register(
            rule_id,
            name=rule_def.name,
            description=rule_def.description,
            severity=rule_def.severity,
            layer=rule_def.layer,
            checker=checker,
        )

    return engine


def cmd_check(args: argparse.Namespace) -> int:
    """Run lint checks and return exit code (0=clean, 1=issues found)."""
    from toolint.formatters import format_json, format_text

    engine = _get_engine()

    select = args.select.split(",") if args.select else None
    ignore = args.ignore.split(",") if args.ignore else None

    results = engine.check(args.path, select=select, ignore=ignore)

    if args.output_format == "json":
        print(format_json(results))
    else:
        print(format_text(results))

    # Exit code: 1 if any errors
    from toolint.core.models import Severity

    has_errors = any(r.severity == Severity.ERROR for r in results)
    return 1 if has_errors else 0


def cmd_rules() -> None:
    """List all available rules."""
    engine = _get_engine()
    rules = engine.rules

    if not rules:
        print("No rules registered.")
        return

    current_layer = ""
    for rule_id in sorted(rules):
        rule = rules[rule_id]
        if rule.layer != current_layer:
            current_layer = rule.layer
            print(f"\n{current_layer.upper()}")
            print("-" * 60)
        print(f"  {rule.id}  ({rule.severity})  {rule.description}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "check":
        exit_code = cmd_check(args)
        sys.exit(exit_code)
    elif args.command == "rules":
        cmd_rules()


if __name__ == "__main__":
    main()
