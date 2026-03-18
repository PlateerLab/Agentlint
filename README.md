# Toolint

[![CI](https://github.com/PlateerLab/Toolint/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/Toolint/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/toolint)](https://pypi.org/project/toolint/)
[![Python](https://img.shields.io/pypi/pyversions/toolint)](https://pypi.org/project/toolint/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Structural linter for Python agent tool packages.**

Ensures your package works as a **library**, **CLI**, and **MCP server** simultaneously — with zero-dependency core and proper facade separation.

## The Problem

AI agent tools need to work in multiple contexts at once:

```python
# As a library
from my_tool import MyTool
tool = MyTool()
tool.search("query")
```

```bash
# As a CLI
my-tool search "query"

# As an MCP server
my-tool serve --source spec.json
```

Getting this right requires strict architectural discipline. Without it:

- `core/` imports `numpy` → users get `ImportError` just from `import my_tool`
- MCP server has business logic → can't reuse the same functionality as a library
- CLI calls internal modules directly → refactoring breaks everything
- Tool function has no docstring → LLM can't select the right tool
- `__version__` doesn't match `pyproject.toml` → PyPI shows wrong version

**Toolint catches all of these statically, before they reach users.**

## Installation

```bash
pip install toolint

# or run without installing
uvx toolint check .
```

## Usage

```bash
# Lint a project
toolint check .

# Select specific rules
toolint check . --select ATL101,ATL201

# Ignore rules
toolint check . --ignore ATL105,ATL501

# JSON output for CI
toolint check . --format json

# List all rules
toolint rules
```

## Real-World Example

Running `toolint` against [graph-tool-call](https://github.com/SonAIengine/graph-tool-call) (graph-based tool retrieval engine for LLM agents):

```
graph_tool_call/core/graph.py:9:4  ATL101 (error)
  Third-party import 'networkx' in core module — core/ must be stdlib-only.
  Move this module outside of core/, or add 'networkx' to
  core_allowed_imports in [tool.toolint].

graph_tool_call/mcp_server.py:128:0  ATL201 (warning)
  'mcp_server.py' imports internal module 'graph_tool_call.retrieval.engine'
  instead of using facade 'ToolGraph'.

graph_tool_call/tool_graph.py:410:0  ATL501 (warning)
  Facade method 'ToolGraph.add_domain()' has no docstring.

11 issues found (1 error, 10 warnings)
```

## Architecture Enforced

Toolint validates this package structure:

```
my_package/
├── __init__.py          # __version__, __all__, lazy imports
├── __main__.py          # CLI — calls facade only
├── core/                # stdlib ONLY — no external deps
│   ├── protocol.py      # Abstract interfaces (Protocol)
│   └── models.py        # Domain models (dataclass)
├── feature_a/           # Business logic (optional deps with guards)
├── facade.py            # Single public API class
├── mcp_server.py        # MCP server — wraps facade
└── middleware.py         # SDK patches — wraps facade
```

Four principles:

1. **Core is stdlib-only** — `import my_tool` always works, no extras needed
2. **Facade is the single API** — CLI, MCP, middleware all go through one class
3. **Optional deps use import guards** — graceful degradation, not crashes
4. **Interface layers are thin** — no business logic in MCP server or CLI

## Rules

### Structure (ATL0xx)

| Rule | Sev | What it checks |
|------|-----|----------------|
| `ATL001` | error | Facade class exists in the package |
| `ATL002` | error | `__main__.py` exists |
| `ATL003` | warn | `__init__.py` has `__all__` with facade class |
| `ATL004` | error | `__version__` matches `pyproject.toml` |

### Dependencies (ATL1xx)

| Rule | Sev | What it checks |
|------|-----|----------------|
| `ATL101` | error | `core/` has no third-party imports |
| `ATL102` | error | Optional deps use `try/except ImportError` |
| `ATL103` | warn | Import guard has install hint message |
| `ATL104` | error | Guarded imports are in `pyproject.toml` extras |
| `ATL105` | warn | `__init__.py` doesn't eagerly import optional deps |

### Layer Separation (ATL2xx)

| Rule | Sev | What it checks |
|------|-----|----------------|
| `ATL201` | warn | Interface files go through facade, not internal modules |
| `ATL202` | warn | CLI references the facade class |
| `ATL203` | warn | Interface doesn't import `core/` directly (types allowed) |

### pyproject.toml (ATL3xx)

| Rule | Sev | What it checks |
|------|-----|----------------|
| `ATL301` | error | CLI scripts entry registered |
| `ATL302` | error | MCP server present → `mcp` extras defined |
| `ATL303` | warn | `all` extras includes everything |

### Schema Quality (ATL5xx)

| Rule | Sev | What it checks |
|------|-----|----------------|
| `ATL501` | warn | Facade public methods have docstrings |
| `ATL502` | warn | Facade public methods have type hints |
| `ATL503` | error | MCP tool functions have docstrings (min 10 chars) |
| `ATL504` | warn | MCP tool docstrings describe parameters |

## Configuration

```toml
# pyproject.toml
[tool.toolint]
package = "my_tool"              # auto-detected
facade_class = "MyTool"          # auto-detected
core_dir = "core"                # default
interface_files = [              # default
    "mcp_server.py", "mcp_proxy.py",
    "middleware.py", "__main__.py"
]
core_allowed_imports = []        # escape hatch for core/
ignore = ["ATL105"]              # rules to skip
```

Or use `.toolint.toml` as a standalone config file.

## CI Integration

### GitHub Actions

```yaml
- name: Structural lint
  run: uvx toolint check .
```

### Pre-commit

```yaml
repos:
  - repo: https://github.com/PlateerLab/Toolint
    rev: v0.1.0
    hooks:
      - id: toolint
```

## How It Works

- **Zero dependencies** — stdlib only (`ast`, `tomllib`, `pathlib`)
- **AST-based** — parses Python files without importing them
- **Fast** — 66 tests run in 0.1s, real projects lint in milliseconds
- **Python 3.10+** — uses `sys.stdlib_module_names` for accurate stdlib detection

## Links

- [PyPI](https://pypi.org/project/toolint/)
- [GitHub](https://github.com/PlateerLab/Toolint)
- [graph-tool-call](https://github.com/SonAIengine/graph-tool-call) — reference architecture this linter validates

## License

MIT
