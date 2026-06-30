# Costwise Phase 4 Summary: Integration Layer

> **Load this document + `COSTWISE_PLAN.md` at the start of a new session to resume with Phase 5.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Python venv: `.venv/` (Python 3.14, `uv pip install -e ".[proxy,dev]"`)

---

## What Phase 4 Built

Phase 4 connects Costwise to its four companion tools (Graphify, Headroom, RTK, Ponytail) plus a LiteLLM callback adapter. Each integration is optional — graceful fallback when the dependency is absent.

**Cost equation so far:**
- Phase 1 (routing): cheaper model → ~50% model cost savings
- Phase 2 (pruning): fewer tokens → ~33-45% token savings
- Phase 3 (arbitrage + budget): cheapest *healthy* provider + spend caps
- Phase 4 (integrations): Headroom compression + RTK shell savings + Ponytail output reduction + unified tracking
- Combined: RTK filters → Costwise routes + prunes → Headroom compresses → cheapest provider

---

## Complete File Tree After Phase 4

```
src/costwise/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── main.py
│   └── gain_cmd.py
├── config/
│   ├── __init__.py
│   ├── loader.py
│   └── schema.py               # MODIFIED: +IntegrationsConfig
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
├── graph/
│   ├── __init__.py
│   ├── loader.py
│   ├── relevance.py
│   ├── pruner.py
│   └── cache.py
├── integrations/               # ← ALL NEW
│   ├── __init__.py
│   ├── graphify.py             # MCP client for dynamic graph queries
│   ├── headroom.py             # CompressionHooks subclass + compress wrapper
│   ├── rtk.py                  # Read-only SQLite reader for RTK savings
│   ├── ponytail.py             # Config reader, output savings estimation
│   └── litellm.py              # LiteLLM callback adapter
├── proxy/
│   ├── __init__.py
│   ├── health.py
│   ├── server.py               # MODIFIED: +Headroom compression step, +compression headers
│   └── translator.py
└── tracking/
    ├── __init__.py
    ├── schema.sql
    └── store.py

tests/
├── conftest.py
├── test_graph_loader.py         # 9 tests
├── test_graph_relevance.py      # 11 tests
├── test_graph_pruner.py         # 6 tests
├── test_graph_cache.py          # 8 tests
├── test_health.py               # 16 tests
├── test_budget.py               # 11 tests
├── test_arbitrage_health.py     # 11 tests
├── test_integration_ponytail.py # 15 tests ← NEW
├── test_integration_rtk.py      # 10 tests ← NEW
├── test_integration_headroom.py # 11 tests ← NEW
├── test_integration_graphify.py # 9 tests  ← NEW
└── test_integration_litellm.py  # 11 tests ← NEW
```

---

## Module API Reference

### `integrations/ponytail.py`

**Data classes:**
- `PonytailConfig(mode, enabled, config_path, output_savings_ratio)` — frozen

**Classes:**
- `PonytailReader(config_path="")`
  - `get_config() → PonytailConfig`
  - `get_mode() → str | None`
  - `estimate_output_savings(mode) → float` (static)
  - `adjust_output_tokens(estimated_output_tokens, mode) → int` (static)

**Functions:**
- `get_ponytail_mode(config_path="") → str | None` — standalone drop-in

**Savings by mode:** off=0%, lite=20%, full=40%, ultra=54%

### `integrations/rtk.py`

**Data classes:**
- `RtkSummary(total_commands, total_input_tokens, total_output_tokens, total_saved_tokens, avg_savings_pct, total_exec_time_ms)` — frozen
- `RtkDailyStats(date, commands, saved_tokens, savings_pct)` — frozen

**Classes:**
- `RtkReader(db_path="")`
  - `find_db() → Path` (classmethod) — platform-aware DB detection
  - `available → bool` — True if DB file exists
  - `get_summary(project_path=None, since=None) → RtkSummary`
  - `get_daily_savings(days=30, project_path=None) → list[RtkDailyStats]`
  - `close()`

**DB path:** `~/.local/share/rtk/tracking.db` (Linux), `~/Library/Application Support/rtk/tracking.db` (macOS)

### `integrations/headroom.py`

**Data classes:**
- `CompressionResult(messages, tokens_before, tokens_after, tokens_saved, compression_ratio, applied)` — frozen

**Classes:**
- `CostwiseCompressionHooks(CompressionHooks)` — graph-aware bias computation
  - `compute_biases(messages, ctx) → dict[int, float]` — relevance→bias mapping
  - `post_compress(event)` — stores last event for metrics
  - `last_event → CompressEvent | None`

**Functions:**
- `is_available() → bool` — True if Headroom is importable
- `compress_messages(messages, model, relevance_scores=None) → CompressionResult`
  - Falls back to passthrough if Headroom not installed or on error

### `integrations/graphify.py`

**Data classes:**
- `GraphQueryResult(text, nodes_visited, tool_name)` — frozen

**Classes:**
- `GraphifyClient(graph_path="graphify-out/graph.json")`
  - `running → bool`
  - `query_graph(question, mode="bfs", depth=3, token_budget=2000) → GraphQueryResult | None`
  - `get_node(label) → GraphQueryResult | None`
  - `get_neighbors(label, relation_filter=None) → GraphQueryResult | None`
  - `get_community(community_id) → GraphQueryResult | None`
  - `graph_stats() → GraphQueryResult | None`
  - `close()` — terminates subprocess

**Protocol:** JSON-RPC over stdio (MCP transport), spawns `python -m graphify.serve`

### `integrations/litellm.py`

**Classes:**
- `CostwiseCallback(store, session_id="litellm")`
  - `async_success_handler(kwargs, response, start_time, end_time)` — records RoutingRecord
  - `async_failure_handler(kwargs, exception, start_time, end_time)` — records error

**Usage:** `litellm.callbacks = [CostwiseCallback(store)]`

---

## Config Reference

```toml
[costwise.integrations]
graphify_mcp = false              # Spawn Graphify MCP server for dynamic queries
graphify_graph_path = ""          # Override graph path for MCP server
headroom_enabled = true           # Compress messages after pruning
headroom_proxy_chain = false      # Chain to Headroom proxy instead of in-process
headroom_proxy_url = "http://127.0.0.1:8787"
rtk_enabled = true                # Merge RTK savings into dashboard
rtk_db_path = ""                  # Override (auto-detected if empty)
ponytail_enabled = true           # Read Ponytail mode for routing signals
ponytail_config_path = ""         # Override (default ~/.config/ponytail/config.json)
```

---

## Proxy Request Pipeline (after Phase 4)

```
Request arrives at proxy
    │
    ├── 1. Parse request body (JSON)
    ├── 2. Get graph from cache (GraphCache.get())
    ├── 3. Extract signals (signals.py)
    ├── 4. Classify complexity → SIMPLE / MEDIUM / COMPLEX
    ├── 5. Budget check → ALLOW / WARN / DOWNGRADE / BLOCK
    ├── 6. Route to optimal model (health-aware arbitrage)
    ├── 7. Prune context (graph-guided, removes whole messages)
    ├── 7.5. Compress via Headroom (token-level, within messages)  ← NEW IN PHASE 4
    │         └── Only if headroom_enabled + Headroom installed
    ├── 8. Translate format if cross-provider
    ├── 9. Forward to provider API
    │       └── If 429: retry with fallback chain
    ├── 10. Record provider health
    ├── 11. Record spend to budget enforcer
    ├── 12. Record to SQLite
    └── 13. Return response with headers:
            x-costwise-routed: <model>
            x-costwise-tier: <SIMPLE|MEDIUM|COMPLEX>
            x-costwise-pruned: <tokens_saved>
            x-costwise-compressed: <tokens_saved>             ← NEW
            x-costwise-budget-action: <allow|warn|downgrade|block>
            x-costwise-budget-warning: <message>
```

---

## Key Design Decisions

1. **Every integration is optional.** Each module checks for its dependency at import time and provides graceful fallback. Costwise works with zero companion tools installed — each integration adds value independently.

2. **Read-only RTK access.** The `RtkReader` opens RTK's SQLite in read-only mode (`?mode=ro` URI). Costwise never writes to another tool's database.

3. **Headroom in-process over proxy chain.** The default is in-process compression via `headroom.compress()`, which enables graph-aware bias hooks. Proxy chaining (config flag) is available for users who already run Headroom's proxy separately.

4. **Prune before compress.** Costwise's graph-guided pruner removes entire messages (coarse), then Headroom compresses within remaining messages (fine-grained). This prevents wasting compression compute on messages that would have been pruned.

5. **Graphify MCP is opt-in.** Unlike other integrations (which auto-detect), the MCP client spawns a subprocess. Opt-in (`graphify_mcp = true`) because subprocess management has lifecycle implications.

6. **LiteLLM adapter is proxy-independent.** Users can get Costwise tracking without running the proxy — just add the callback to LiteLLM. This supports gradual adoption.

7. **Ponytail output intelligence.** Beyond mode detection (already in signals.py), the new module estimates output token savings per mode. This enables Ponytail-aware cost estimates: when output tokens are reduced, models with cheap input pricing become more attractive.

---

## Validation Results

| Metric | Target | Actual |
|--------|--------|--------|
| Test suite | All passing | 128/128 (0.47s) |
| Original tests | No regressions | 72/72 pass |
| New integration tests | All passing | 56/56 pass |
| Ponytail: mode detection | All 4 modes | OK |
| Ponytail: missing/invalid config | Graceful fallback | OK |
| RTK: summary + daily stats | Correct aggregation | OK |
| RTK: project filtering | GLOB match | OK |
| RTK: missing DB | FileNotFoundError | OK |
| Headroom: hooks bias mapping | Relevance → bias | OK |
| Headroom: compress fallback | Passthrough when not installed | OK |
| Headroom: exception handling | Logs and passes through | OK |
| Graphify: MCP init + query | JSON-RPC over stdio | OK |
| Graphify: error handling | Returns None on MCP error | OK |
| Graphify: close | Terminates subprocess | OK |
| LiteLLM: success recording | Full RoutingRecord | OK |
| LiteLLM: failure recording | Status code + error | OK |
| LiteLLM: exception safety | Swallows record errors | OK |
| Proxy: compression header | x-costwise-compressed | OK |

---

## What's Next: Phase 5 (Dashboard + MCP Server)

Per `COSTWISE_PLAN.md`:

### Files to create:
```
src/costwise/
├── dashboard/
│   ├── app.py             # FastAPI + HTMX: real-time cost dashboard
│   └── templates/         # Jinja2: savings charts, model distribution, budget alerts
├── mcp/
│   └── server.py          # FastMCP: costwise_route, costwise_stats, costwise_gain
└── cli/
    ├── wrap_cmd.py        # `costwise wrap claude` auto-configuration
    └── doctor_cmd.py      # Health checks for all integration points
```

### What already exists for Phase 5:
- `integrations/rtk.py` provides RTK savings data for the unified dashboard
- `integrations/ponytail.py` provides output savings data
- `integrations/headroom.py` provides compression metrics
- `tracking/store.py` has `get_gain_summary()` and `get_session_stats()`
- The proxy already emits headers for all cost layers

---

## Environment

- Python 3.14 venv at `.venv/`
- Dependencies: `uv pip install -e ".[proxy,dev]"`
- CLI: `costwise --version` → 0.1.0
- Tests: `.venv/bin/python -m pytest tests/ -v`
- Proxy version: 0.3.0 (Phase 4 additions are integration-only, no proxy version bump)
