# Agentlint

**Structural linter for MCP-compatible, zero-dependency Python agent tool packages.**

`agentlint` enforces architectural rules that ensure your Python package works correctly as:
- A **standalone library** (`from my_tool import MyTool`)
- A **CLI tool** (`my-tool search "query"`)
- An **MCP server** (`my-tool serve --source spec.json`)
- An **SDK middleware** (OpenAI / Anthropic client patches)

Inspired by the architecture of [graph-tool-call](https://github.com/SonAIengine/graph-tool-call).

## Why?

Building agent-compatible tools is easy to get wrong:

| Mistake | Consequence |
|---------|------------|
| `core/` imports `numpy` without guard | Users without numpy get `ImportError` on `import my_tool` |
| MCP server has business logic | Can't use the same functionality as a library |
| `__version__` != `pyproject.toml` version | PyPI shows wrong version |
| Tool function has no docstring | LLM can't understand what the tool does |
| Optional dep not in `extras` | `pip install my-tool[mcp]` doesn't install MCP SDK |

`agentlint` catches all of these **before** they reach users.

## Installation

```bash
pip install agentlint

# or with uv
uv pip install agentlint

# or as a tool
uvx agentlint check .
```

## Quick Start

```bash
# Lint current project
agentlint check .

# Lint with specific rules only
agentlint check . --select ATL101,ATL102

# Ignore specific rules
agentlint check . --ignore ATL105

# JSON output (for CI integration)
agentlint check . --format json

# Show all available rules
agentlint rules
```

## Example Output

```
my_tool/core/engine.py:3:0  ATL101 (error)
  Hard import of 'numpy' in core module — core must be stdlib-only.
  Use try/except ImportError guard or move to a non-core module.

my_tool/retrieval/embedding.py:5:0  ATL102 (error)
  Optional import 'sentence_transformers' missing try/except ImportError guard.

my_tool/__init__.py:1:0  ATL004 (error)
  Version mismatch: __init__.py has '0.2.0' but pyproject.toml has '0.2.1'

my_tool/mcp_server.py:42:8  ATL503 (error)
  MCP tool function 'process_data' has no docstring.
  LLMs rely on tool descriptions to select the right tool.

4 issues found (4 errors, 0 warnings)
```

## Rules

### Layer 1: Structure (ATL0xx)

| Rule | Severity | Description |
|------|----------|-------------|
| `ATL001` | error | Package must have a single public facade class |
| `ATL002` | error | `__main__.py` must exist and be registered in pyproject.toml scripts |
| `ATL003` | warning | `__init__.py` must define `__all__` including the facade class |
| `ATL004` | error | `__version__` in `__init__.py` must match pyproject.toml version |

### Layer 2: Dependency Rules (ATL1xx)

| Rule | Severity | Description |
|------|----------|-------------|
| `ATL101` | error | No third-party imports in `core/` directory (stdlib only) |
| `ATL102` | error | Optional dependencies must use `try/except ImportError` guard |
| `ATL103` | warning | Import guard must include install hint (e.g. `pip install pkg[extra]`) |
| `ATL104` | error | Optional imports must be registered in pyproject.toml `extras` |
| `ATL105` | warning | `__init__.py` should not eagerly import optional-dep modules |

### Layer 3: Layer Separation (ATL2xx)

| Rule | Severity | Description |
|------|----------|-------------|
| `ATL201` | warning | Interface files should not call internal business logic directly (type/enum/constant imports are allowed) |
| `ATL202` | warning | CLI command handlers should invoke functionality through the facade class |
| `ATL203` | warning | Interface layer should not import `core/` internals directly (except types/constants) |

### Layer 4: pyproject.toml Consistency (ATL3xx)

| Rule | Severity | Description |
|------|----------|-------------|
| `ATL301` | error | CLI entry point must be registered in `[tool.poetry.scripts]` or `[project.scripts]` |
| `ATL302` | error | If MCP server exists, `mcp` extras group must be defined |
| `ATL303` | warning | `all` extras group must include all dependencies from other extras groups |

### Layer 5: Tool Schema Quality (ATL5xx)

| Rule | Severity | Description |
|------|----------|-------------|
| `ATL501` | warning | Facade public methods must have docstrings |
| `ATL502` | warning | Facade public methods must have parameter + return type hints |
| `ATL503` | error | MCP tool functions must have docstrings (min 10 chars) |
| `ATL504` | warning | MCP tool function docstrings should describe each parameter |

### Layer 6: Agent Compatibility (ATL6xx)

| Rule | Severity | Description |
|------|----------|-------------|
| `ATL601` | warning | Facade public methods should return JSON-serializable types |
| `ATL602` | error | MCP tool functions must return `str` (MCP protocol requirement) |
| `ATL603` | warning | Facade/MCP tools should not silently swallow exceptions |

## Configuration

Add to `pyproject.toml`:

```toml
[tool.agentlint]
# Package root (auto-detected from pyproject.toml)
package = "my_tool"

# Facade class name (auto-detected if single prominent class exists)
facade_class = "MyTool"

# Core directory (default: "core")
core_dir = "core"

# Interface files (default: auto-detected)
interface_files = ["mcp_server.py", "mcp_proxy.py", "middleware.py", "__main__.py"]

# Extra stdlib-like packages allowed in core (escape hatch)
core_allowed_imports = []

# Rules to ignore
ignore = ["ATL105"]
```

Or use a standalone file `.agentlint.toml` with the same structure (without the `[tool.agentlint]` nesting).

## The Architecture This Enforces

```
my_package/
├── __init__.py          # __version__, __all__, lazy imports
├── __main__.py          # CLI (argparse) — calls facade only
├── core/                # ZERO external dependencies (stdlib only)
│   ├── protocol.py      # Abstract interfaces (Protocol classes)
│   └── models.py        # Domain models (dataclasses)
├── feature_a/           # Business logic modules
│   └── ...              # May use optional deps with import guards
├── facade.py            # Single public API class
├── mcp_server.py        # MCP server — wraps facade only
└── middleware.py         # SDK patches — wraps facade only
```

**Key principles:**
1. **Core is stdlib-only** — anyone can `import my_tool` without installing extras
2. **Facade is the single API surface** — all interfaces (CLI, MCP, middleware) go through it
3. **Optional deps use import guards** — graceful degradation, not crashes
4. **Interface layers are thin wrappers** — no business logic in MCP server or CLI

## CI Integration

### GitHub Actions

```yaml
- name: Lint agent tool structure
  run: uvx agentlint check .
```

### Pre-commit

```yaml
repos:
  - repo: https://github.com/PlateerLab/agentlint
    rev: v0.1.0
    hooks:
      - id: agentlint
```

## Technical Details

- **Python 3.10+**
- **Zero dependencies** — uses only `ast` and `tomllib` from stdlib
- **Fast** — AST parsing, no runtime imports of the target package
- **Self-validating** — `agentlint` follows the same architecture it enforces

## License

MIT

## Links

- [GitHub](https://github.com/PlateerLab/agentlint)
- [PyPI](https://pypi.org/project/agentlint/) (coming soon)
- [graph-tool-call](https://github.com/SonAIengine/graph-tool-call) — the reference implementation this linter is based on
