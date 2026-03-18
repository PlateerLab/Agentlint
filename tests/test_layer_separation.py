"""Tests for Layer 3 (ATL201-203) and Layer 5 (ATL501-504) rules."""

from __future__ import annotations

from pathlib import Path

from agentlint.core.config import load_config
from agentlint.core.models import LintConfig
from agentlint.rules import layer_separation, schema_quality


def _make_project(tmp_path: Path, pkg: str = "my_tool", files: dict[str, str] | None = None):
    """Create a minimal project structure for testing."""
    pyproject = tmp_path / "pyproject.toml"
    if not pyproject.exists():
        pyproject.write_text(
            f'[tool.poetry]\nname = "{pkg.replace("_", "-")}"\nversion = "0.1.0"\n'
            f'packages = [{{include = "{pkg}"}}]\n\n'
            f'[tool.poetry.dependencies]\npython = "^3.10"\n\n'
            f'[tool.poetry.scripts]\n{pkg.replace("_", "-")} = "{pkg}.__main__:main"\n'
        )

    pkg_dir = tmp_path / pkg
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text('__version__ = "0.1.0"\n__all__ = ["MyTool"]\n')
    (pkg_dir / "__main__.py").write_text("from my_tool import MyTool\ndef main(): MyTool()\n")

    core_dir = pkg_dir / "core"
    core_dir.mkdir(exist_ok=True)
    (core_dir / "__init__.py").write_text("")

    # Default facade
    (pkg_dir / "facade.py").write_text(
        "class MyTool:\n"
        '    """The main tool."""\n'
        "    def search(self, query: str) -> list:\n"
        '        """Search for tools."""\n'
        "        ...\n"
        "    def ingest(self, source: str) -> None:\n"
        '        """Ingest a source."""\n'
        "        ...\n"
        "    def retrieve(self, query: str) -> list:\n"
        '        """Retrieve tools."""\n'
        "        ...\n"
    )

    if files:
        for name, content in files.items():
            path = pkg_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    return tmp_path


def _config(**kwargs) -> LintConfig:
    kwargs.setdefault("package", "my_tool")
    kwargs.setdefault("facade_class", "MyTool")
    return LintConfig(**kwargs)


def _pyproject(tmp_path: Path) -> dict:
    _, pyproject = load_config(tmp_path)
    return pyproject


# === ATL201: interface no business logic ===


class TestATL201:
    def test_pass_imports_facade(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool.facade import MyTool\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_business_logic(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 0

    def test_pass_imports_public_api(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool import MyTool\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_business_logic(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 0

    def test_pass_type_import(self, tmp_path: Path):
        """Type/enum/constant imports from internal modules are allowed."""
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool.core.schema import NodeType\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_business_logic(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 0

    def test_fail_internal_import(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool.retrieval.engine import search\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_business_logic(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 1
        assert results[0].rule_id == "ATL201"

    def test_pass_interface_imports_interface(self, tmp_path: Path):
        """__main__.py importing mcp_server is allowed."""
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "def run_server(): ...\n",
                "__main__.py": (
                    "from my_tool import MyTool\n"
                    "from my_tool.mcp_server import run_server\n"
                    "def main(): MyTool()\n"
                ),
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_business_logic(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 0


# === ATL202: CLI uses facade ===


class TestATL202:
    def test_pass(self, tmp_path: Path):
        _make_project(tmp_path)
        cfg = _config()
        results = layer_separation.check_cli_uses_facade(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 0

    def test_fail_no_facade_reference(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "__main__.py": "import sys\ndef main(): print('hello')\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_cli_uses_facade(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 1
        assert results[0].rule_id == "ATL202"


# === ATL203: interface no core import ===


class TestATL203:
    def test_pass_no_core_import(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool import MyTool\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_core_import(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 0

    def test_pass_type_import_from_core(self, tmp_path: Path):
        """Type imports from core are allowed."""
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool.core.models import ToolSchema\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_core_import(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 0

    def test_fail_function_import_from_core(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": "from my_tool.core.engine import run_search\n",
            },
        )
        cfg = _config()
        results = layer_separation.check_interface_no_core_import(
            tmp_path, cfg, _pyproject(tmp_path)
        )
        assert len(results) == 1
        assert results[0].rule_id == "ATL203"


# === ATL501: facade docstrings ===


class TestATL501:
    def test_pass_all_documented(self, tmp_path: Path):
        _make_project(tmp_path)  # default facade has docstrings
        cfg = _config()
        results = schema_quality.check_facade_docstrings(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 0

    def test_fail_missing_docstring(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "facade.py": (
                    "class MyTool:\n"
                    "    def search(self): ...\n"
                    "    def ingest(self): ...\n"
                    "    def retrieve(self): ...\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_facade_docstrings(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 3  # 3 methods without docstrings


# === ATL502: facade type hints ===


class TestATL502:
    def test_pass_fully_annotated(self, tmp_path: Path):
        _make_project(tmp_path)  # default facade has type hints
        cfg = _config()
        results = schema_quality.check_facade_type_hints(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 0

    def test_fail_no_return_type(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "facade.py": (
                    "class MyTool:\n"
                    '    """Tool."""\n'
                    "    def search(self, query: str):\n"
                    '        """Search."""\n'
                    "        ...\n"
                    "    def ingest(self, source: str) -> None:\n"
                    '        """Ingest."""\n'
                    "        ...\n"
                    "    def retrieve(self, query: str) -> list:\n"
                    '        """Retrieve."""\n'
                    "        ...\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_facade_type_hints(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 1
        assert "search" in results[0].message
        assert "return" in results[0].message


# === ATL503: MCP tool docstrings ===


class TestATL503:
    def test_pass_with_docstring(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": (
                    "class App:\n"
                    "    def tool(self): return lambda f: f\n"
                    "app = App()\n\n"
                    "@app.tool()\n"
                    "def search_tools(query: str) -> str:\n"
                    '    """Search for tools by natural language query."""\n'
                    "    return ''\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_mcp_tool_docstrings(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 0

    def test_fail_no_docstring(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": (
                    "class App:\n"
                    "    def tool(self): return lambda f: f\n"
                    "app = App()\n\n"
                    "@app.tool()\n"
                    "def search_tools(query: str) -> str:\n"
                    "    return ''\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_mcp_tool_docstrings(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 1
        assert results[0].rule_id == "ATL503"

    def test_skip_protocol_handlers(self, tmp_path: Path):
        """@server.list_tools() and @server.call_tool() are protocol handlers, not user tools."""
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": (
                    "class Server:\n"
                    "    def list_tools(self): return lambda f: f\n"
                    "    def call_tool(self): return lambda f: f\n"
                    "server = Server()\n\n"
                    "@server.list_tools()\n"
                    "async def list_tools(): ...\n\n"
                    "@server.call_tool()\n"
                    "async def call_tool(name, args): ...\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_mcp_tool_docstrings(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 0


# === ATL504: MCP tool param docs ===


class TestATL504:
    def test_pass_with_args_section(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": (
                    "class App:\n"
                    "    def tool(self): return lambda f: f\n"
                    "app = App()\n\n"
                    "@app.tool()\n"
                    "def search(query: str, top_k: int) -> str:\n"
                    '    """Search for tools.\n\n'
                    "    Args:\n"
                    "        query: search text\n"
                    "        top_k: max results\n"
                    '    """\n'
                    "    return ''\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_mcp_tool_param_docs(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 0

    def test_fail_missing_param_docs(self, tmp_path: Path):
        _make_project(
            tmp_path,
            files={
                "mcp_server.py": (
                    "class App:\n"
                    "    def tool(self): return lambda f: f\n"
                    "app = App()\n\n"
                    "@app.tool()\n"
                    "def search(query: str, top_k: int) -> str:\n"
                    '    """Search for tools."""\n'
                    "    return ''\n"
                ),
            },
        )
        cfg = _config()
        results = schema_quality.check_mcp_tool_param_docs(tmp_path, cfg, _pyproject(tmp_path))
        assert len(results) == 1
        assert "query" in results[0].message
