# Costwise Phase 5 Summary: Dashboard + MCP Server

> **Load this document + `COSTWISE_PLAN.md` at the start of a new session to resume with Phase 6.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Python venv: `.venv/` (Python 3.14, `uv pip install -e ".[proxy,dashboard,mcp,dev]"`)

---

## What Phase 5 Built

Phase 5 adds the user-facing layer: a real-time cost dashboard, an MCP server for direct Claude Code integration, and CLI commands for setup/diagnostics.

**Cost equation so far:**
- Phase 1 (routing): cheaper model → ~50% model cost savings
- Phase 2 (pruning): fewer tokens → ~33-45% token savings
- Phase 3 (arbitrage + budget): cheapest *healthy* provider + spend caps
- Phase 4 (integrations): Headroom compression + RTK shell savings + Ponytail output reduction
- Phase 5 (visibility): dashboard for monitoring, MCP for AI-native access, doctor for diagnostics
- Combined: RTK filters → Costwise routes + prunes → Headroom compresses → cheapest provider → dashboard tracks it all

---

## Complete File Tree After Phase 5

```
src/costwise/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── main.py                # MODIFIED: +dashboard, +mcp, +wrap, +doctor commands
│   ├── gain_cmd.py
│   ├── doctor_cmd.py          # ← NEW: 9 health checks
│   └── wrap_cmd.py            # ← NEW: auto-configure Claude Code
├── config/
│   ├── __init__.py
│   ├── loader.py
│   └── schema.py
├── core/
│   ├── __init__.py
│   ├── models.py
│   ├── pricing.py
│   ├── signals.py
│   ├── classifier.py
│   ├── arbitrage.py
│   ├── router.py
│   ├── health.py
│   └── budget.py
├── dashboard/                  # ← ALL NEW
│   ├── __init__.py
│   ├── app.py                 # FastAPI + HTMX dashboard
│   ├── data.py                # DashboardDataCollector
│   ├── charts.py              # Pure Python SVG generators
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   └── partials/
│   │       ├── requests.html
│   │       ├── costs.html
│   │       ├── models.html
│   │       ├── savings.html
│   │       └── budget.html
│   └── static/
│       ├── htmx.min.js        # Vendored HTMX v2.0.4
│       └── style.css          # Dark theme CSS
├── graph/
│   ├── __init__.py
│   ├── loader.py
│   ├── relevance.py
│   ├── pruner.py
│   └── cache.py
├── integrations/
│   ├── __init__.py
│   ├── graphify.py
│   ├── headroom.py
│   ├── rtk.py
│   ├── ponytail.py
│   └── litellm.py
├── mcp/                        # ← ALL NEW
│   ├── __init__.py
│   ├── server.py              # FastMCP: 4 tools
│   └── __main__.py            # python -m costwise.mcp
├── proxy/
│   ├── __init__.py
│   ├── health.py
│   ├── server.py
│   └── translator.py
└── tracking/
    ├── __init__.py
    ├── schema.sql
    └── store.py               # MODIFIED: +5 new query methods

tests/
├── conftest.py
├── test_graph_loader.py         # 9 tests
├── test_graph_relevance.py      # 11 tests
├── test_graph_pruner.py         # 6 tests
├── test_graph_cache.py          # 8 tests
├── test_health.py               # 16 tests
├── test_budget.py               # 11 tests
├── test_arbitrage_health.py     # 11 tests
├── test_integration_ponytail.py # 15 tests
├── test_integration_rtk.py      # 10 tests
├── test_integration_headroom.py # 11 tests
├── test_integration_graphify.py # 9 tests
├── test_integration_litellm.py  # 11 tests
├── test_dashboard_data.py       # 14 tests ← NEW
├── test_dashboard_charts.py     # 13 tests ← NEW
├── test_dashboard_app.py        # 11 tests ← NEW
├── test_mcp_server.py           # 12 tests ← NEW
└── test_cli_commands.py         # 20 tests ← NEW
```

---

## Module API Reference

### `dashboard/app.py`

**Functions:**
- `create_dashboard_app(config: CostwiseConfig, store: TrackingStore) -> FastAPI`

**Routes:**
| Route | Method | Response | Purpose |
|-------|--------|----------|---------|
| `/` | GET | HTML | Main dashboard page |
| `/health` | GET | JSON | Health check |
| `/api/summary` | GET | JSON | Full dashboard data |
| `/api/requests` | GET | JSON | Recent 20 requests |
| `/api/costs` | GET | JSON | Hourly cost series |
| `/api/models` | GET | JSON | Model distribution |
| `/api/health` | GET | JSON | Provider health snapshots |
| `/api/budget` | GET | JSON | Budget status + alerts |
| `/partials/requests` | GET | HTML | HTMX partial: request table |
| `/partials/costs` | GET | HTML | HTMX partial: cost chart SVG |
| `/partials/models` | GET | HTML | HTMX partial: model donut SVG |
| `/partials/savings` | GET | HTML | HTMX partial: savings bars SVG |
| `/partials/budget` | GET | HTML | HTMX partial: budget gauge SVG |

### `dashboard/data.py`

**Data classes:**
- `DashboardData(gain_summary, recent_requests, model_distribution, tier_distribution, hourly_costs, savings_breakdown, budget_alerts, hourly_spend, rtk_summary, rtk_daily, ponytail_config, headroom_available, provider_health)` — frozen

**Classes:**
- `DashboardDataCollector(store, config)`
  - `collect() → DashboardData` — aggregates all sources, catches per-source exceptions

### `dashboard/charts.py`

**Functions:**
- `cost_bar_chart(hourly_data, width=600, height=200) → str` — SVG bar chart
- `model_donut_chart(distribution, width=300, height=300) → str` — SVG donut
- `savings_stacked_bars(breakdown, width=500, height=120) → str` — SVG stacked bars
- `budget_gauge(current_pct, width=200, height=120) → str` — SVG arc gauge

Color palette: emerald, blue, amber, red, violet, orange, green, pink on #1e1e2e background.

### `mcp/server.py`

**Tools (FastMCP):**
| Tool | Args | Returns |
|------|------|---------|
| `costwise_route` | `prompt: str, model: str = "claude-opus-4-7"` | JSON: recommended_model, tier, confidence, reason, savings |
| `costwise_budget` | `files: list[str], token_budget: int = 15000` | JSON: relevant_files (scored), prunable_files, recommendation |
| `costwise_stats` | `session_id: str | None = None` | JSON: total_cost, total_saved, savings_pct, model_distribution |
| `costwise_gain` | (none) | JSON: per-layer savings (routing, pruning, RTK, Ponytail, Headroom) |

**Entry point:** `python -m costwise.mcp` (stdio MCP server)

### `cli/doctor_cmd.py`

**Command:** `costwise doctor [--json-output]`

**Checks:** Config, Tracking DB, Proxy, Dashboard, Graph, RTK, Ponytail, Headroom, Claude Code config

**Output:** Box-drawing checklist with ✓/✗ icons, or JSON array.

### `cli/wrap_cmd.py`

**Command:** `costwise wrap <target> [--dry-run] [--proxy/--no-proxy] [--mcp/--no-mcp]`

**Behavior:** Reads Claude Code settings, injects MCP server entry + proxy URL. Idempotent. Preserves existing config.

---

## New TrackingStore Methods

```python
async def get_model_distribution(self, since: str | None = None) -> list[dict]
async def get_tier_distribution(self, since: str | None = None) -> list[dict]
async def get_hourly_cost_series(self, hours: int = 24) -> list[dict]
async def get_savings_breakdown(self) -> dict
async def get_budget_alerts(self, limit: int = 10) -> list[dict]
```

---

## Config Reference (No Changes from Phase 4)

Dashboard and MCP server use the existing `CostwiseConfig`. No new config keys added — they read from `tracking.db_path`, `integrations.*`, and `budget.*`.

New pyproject.toml extras:
```toml
dashboard = ["jinja2>=3.1"]
mcp = ["mcp>=1.0"]
```

---

## Key Design Decisions

1. **Dashboard is a separate process.** Runs on port 8789 independently of the proxy (port 8788). Can review historical data even when the proxy is stopped. Both read the same SQLite DB via WAL mode.

2. **MCP server accesses SQLite directly.** No dependency on a running proxy. Instantiates its own Router for classification. This means `costwise_route` works even without the proxy.

3. **Server-side SVG charts.** Pure Python, zero JS beyond HTMX (~50KB vendored). Charts render as SVG strings embedded in HTML. No build step, no npm, no CDN dependency.

4. **HTMX polling at 5s intervals.** Each dashboard panel declares `hx-trigger="load, every 5s"`. Simpler than SSE, adequate for a local single-user dashboard.

5. **Starlette 1.3+ TemplateResponse API.** Uses `TemplateResponse(request, name, context)` (new API), not the deprecated `TemplateResponse(name, {"request": request, ...})`.

6. **`costwise wrap` is idempotent.** Running it twice produces the same config. It never deletes existing settings — only adds/updates the `costwise` MCP entry and proxy URL.

7. **Doctor checks are independent.** Each check catches its own exceptions so one failure doesn't prevent reporting the rest. The output matches `costwise gain`'s box-drawing style.

---

## Validation Results

| Metric | Target | Actual |
|--------|--------|--------|
| Test suite | All passing | 198/198 (1.06s) |
| Original tests | No regressions | 128/128 pass |
| New Phase 5 tests | All passing | 70/70 pass |
| Dashboard: index page | Returns HTML | OK |
| Dashboard: API endpoints | Return JSON | OK (6/6) |
| Dashboard: HTMX partials | Return HTML with SVG | OK (5/5) |
| Dashboard: health endpoint | Returns ok | OK |
| MCP: costwise_route | Returns routing JSON | OK |
| MCP: costwise_budget | Returns relevance scores | OK |
| MCP: costwise_stats | Returns aggregated stats | OK |
| MCP: costwise_gain | Returns multi-layer savings | OK |
| CLI: doctor | Runs all 9 checks | OK |
| CLI: doctor --json-output | Valid JSON array | OK |
| CLI: wrap --dry-run | Shows diff, no writes | OK |
| CLI: wrap idempotent | Same config on re-run | OK |
| CLI: wrap preserves config | Existing keys untouched | OK |
| CLI: dashboard command | Registered and help works | OK |
| CLI: mcp command | Registered and help works | OK |
| Charts: empty data handling | Placeholder SVG | OK |
| Charts: SVG validity | Starts with <svg>, has </svg> | OK |

---

## What's Next: Phase 6 (Quality Feedback Loop)

Per `COSTWISE_PLAN.md`:

### Goal: Self-improving routing accuracy.

- Detect retries (same conversation hash + similar content within time window)
- Record retries as classification errors → auto-tune thresholds
- Track false-downgrade rate (COMPLEX task sent to cheap model) — target <3%
- Optional: integrate RouteLLM matrix-factorization classifier for ML-based routing

### What already exists for Phase 6:
- `tracking/store.py` records every routing decision with session_id, tier, status_code
- `core/classifier.py` has configurable `ClassifierConfig` with tunable thresholds
- `core/router.py` has `route_from_signals()` for testing classification in isolation
- `mcp/server.py` can expose feedback tools for agents to report routing quality
- The dashboard can visualize retry rates and false-downgrade metrics

---

## Environment

- Python 3.14 venv at `.venv/`
- Dependencies: `uv pip install -e ".[proxy,dashboard,mcp,dev]"`
- CLI: `costwise --version` → 0.1.0
- Tests: `.venv/bin/python -m pytest tests/ -v`
- Dashboard: `costwise dashboard` → starts on :8789
- MCP: `python -m costwise.mcp` → stdio server
- Proxy version: 0.3.0
