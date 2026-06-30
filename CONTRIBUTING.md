# Contributing to Costwise

Thanks for your interest in contributing! This guide covers setup, testing, and PR guidelines.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/costwise.git
cd costwise

# Create a virtual environment (Python 3.10+)
python -m venv .venv
source .venv/bin/activate

# Install with all extras in editable mode
pip install -e ".[all]"
```

## Running Tests

```bash
# Run all 256 tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_budget.py -v

# Run with coverage
pytest tests/ --cov=costwise --cov-report=term-missing
```

## Linting

```bash
# Check for issues
ruff check src/ tests/

# Auto-fix what's possible
ruff check src/ tests/ --fix

# Format
ruff format src/ tests/
```

Ruff config is in `pyproject.toml`: Python 3.10 target, 100-char line length, E/F/I/UP rules.

## Code Style

- **No comments** unless explaining a non-obvious *why* (hidden constraint, workaround, surprising behavior)
- Never explain *what* the code does — use clear names instead
- No references to tickets, issues, or callers in comments
- 100-character line length
- Type annotations on all public functions
- Pydantic models for data structures, dataclasses for internal config

## Architecture

Costwise has 7 packages, each with a clear responsibility:

| Package | Responsibility |
|---------|---------------|
| `core/` | Models, classifier, router, arbitrage engine, pricing registry, budget enforcer, health tracker |
| `proxy/` | FastAPI proxy server, OpenAI-format request translator |
| `graph/` | Code graph loader (NetworkX), BFS relevance scorer, context pruner, in-memory cache |
| `feedback/` | Retry detector (fingerprint comparison), metrics aggregator, threshold auto-tuner |
| `dashboard/` | HTMX web app, SVG chart generator, SQLite data queries |
| `mcp/` | MCP server with 5 tools (route, budget, stats, gain, feedback) |
| `integrations/` | Adapters for RTK, Ponytail, Headroom, Graphify, LiteLLM |

Supporting packages:
- `config/` — TOML loader + Pydantic schema
- `tracking/` — SQLite store for routing decisions and cost data
- `cli/` — Click CLI with 6 commands

## PR Process

1. Fork the repo and create a feature branch
2. Make your changes
3. Run `ruff check src/ tests/` and fix any issues
4. Run `pytest tests/ -v` and ensure all tests pass
5. Write tests for new functionality
6. Open a PR with a clear description of what changed and why

## Adding a New Provider

1. Add `ModelInfo` entries to `src/costwise/core/pricing.py`
2. Add the provider's API base to `_PROVIDER_API_BASES` in `src/costwise/core/router.py`
3. Add `api_key_env` to the example in `costwise.example.toml`
4. Add tests in `tests/`

## Adding an MCP Tool

1. Add the tool function in `src/costwise/mcp/server.py` with `@mcp.tool()` decorator
2. Include clear docstring with Args/Returns sections (MCP uses these for tool descriptions)
3. Add tests in `tests/test_mcp_server.py`

## Questions?

Open an issue for questions, bug reports, or feature requests.
