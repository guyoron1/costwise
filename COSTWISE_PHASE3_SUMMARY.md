# Costwise Phase 3 Summary: Provider Arbitrage + Budget Policies

> **Load this document + `COSTWISE_PLAN.md` at the start of a new session to resume with Phase 4.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Python venv: `.venv/` (Python 3.14, `uv pip install -e ".[proxy,dev]"`)

---

## What Phase 3 Built

Phase 3 adds **provider health tracking** (circuit breaker), **budget enforcement** (spend limits with auto-downgrade), and **fallback chains** (automatic retry on rate limit). These make the routing proxy production-resilient: it reacts to provider failures, respects cost budgets, and gracefully handles rate limits.

**Cost equation so far:**
- Phase 1 (routing): cheaper model → ~50% model cost savings
- Phase 2 (pruning): fewer tokens → ~33-45% token savings
- Phase 3 (arbitrage + budget): cheapest *healthy* provider + spend caps → controlled cost ceiling
- Combined: fewer tokens × cheapest healthy model × budget guardrails

---

## Complete File Tree After Phase 3

```
src/costwise/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── main.py              # Click: `costwise proxy`, `costwise gain`
│   └── gain_cmd.py          # Token usage summary from SQLite
├── config/
│   ├── __init__.py
│   ├── loader.py            # TOML config with env var interpolation
│   └── schema.py            # Pydantic v2 — all config models
├── core/
│   ├── __init__.py
│   ├── models.py            # Tier, RoutingDecision, CostEstimate, ModelInfo, SignalBundle (MODIFIED: +budget_action, +budget_warning, +fallback_chain)
│   ├── pricing.py           # 10 models pricing registry
│   ├── signals.py           # Extract 8 classification signals from request bodies
│   ├── classifier.py        # Weighted scorer → SIMPLE|MEDIUM|COMPLEX
│   ├── arbitrage.py         # Cross-provider cheapest model selection (MODIFIED: +health filtering, +fallback_chain, +skipped_unhealthy)
│   ├── router.py            # Orchestrator: signals → classify → budget → arbitrage (MODIFIED: +BudgetEnforcer, +ProviderHealthTracker)
│   ├── health.py            # ← NEW: Provider health tracker (circuit breaker, sliding window)
│   └── budget.py            # ← NEW: Budget enforcement (hourly/session limits, auto-downgrade)
├── graph/
│   ├── __init__.py
│   ├── loader.py            # Parse Graphify's graph.json → CodeGraph
│   ├── relevance.py         # BFS relevance scoring + community boost + graph_complexity
│   ├── pruner.py            # Drop low-relevance context entries from messages
│   └── cache.py             # Thread-safe in-memory graph cache, reload on file change
├── proxy/
│   ├── __init__.py
│   ├── health.py            # GET /health, GET /ready
│   ├── server.py            # v0.3.0 — classifies, routes, budget-checks, prunes, retries on 429 (MODIFIED)
│   └── translator.py        # OpenAI ↔ Anthropic format translation
└── tracking/
    ├── __init__.py
    ├── schema.sql            # DDL (provider_health + budget_alerts tables, already existed)
    └── store.py              # SQLite WAL mode (MODIFIED: +record_provider_health, +record_budget_alert, +get_hourly_spend, +get_session_spend, +get_provider_health_stats)

tests/
├── conftest.py              # Shared fixtures
├── test_graph_loader.py     # 9 tests
├── test_graph_relevance.py  # 11 tests
├── test_graph_pruner.py     # 6 tests
├── test_graph_cache.py      # 8 tests
├── test_health.py           # 16 tests — circuit breaker, sliding window, cooldown, degraded
├── test_budget.py           # 11 tests — limits, warnings, downgrade chains, blocking
└── test_arbitrage_health.py # 11 tests — health-aware selection, fallback chains, router budget integration
```

---

## Module API Reference

### `core/health.py`

**Enums:**
- `ProviderStatus(HEALTHY, DEGRADED, UNHEALTHY)`

**Data classes:**
- `HealthEvent(timestamp, latency_ms, status_code, rate_limited, error)` — frozen, slotted
- `ProviderHealthSnapshot(provider, status, total_requests, error_count, rate_limit_count, avg_latency_ms, p95_latency_ms, error_rate, last_rate_limit_at, cooldown_remaining_s)`

**Classes:**
- `ProviderHealthTracker` — thread-safe, in-memory sliding-window tracker
  - Constructor params: `window_seconds=300`, `rate_limit_cooldown_s=30`, `error_rate_threshold=0.50`, `consecutive_error_limit=5`, `latency_threshold_ms=30000`, `min_requests_for_health=3`
  - `record_success(provider, latency_ms, status_code=200)`
  - `record_error(provider, latency_ms, status_code, error="")`
  - `record_rate_limit(provider, latency_ms=0.0)`
  - `is_healthy(provider) → bool`
  - `get_status(provider) → ProviderStatus`
  - `get_snapshot(provider) → ProviderHealthSnapshot`
  - `get_all_snapshots() → dict[str, ProviderHealthSnapshot]`
  - `healthy_providers(candidates: set[str]) → set[str]`
  - `reset(provider=None)` — reset one or all

**Unhealthy triggers (any one):**
1. Rate limited within cooldown period (default 30s)
2. Consecutive errors ≥ limit (default 5)
3. Error rate ≥ threshold (default 50%) in window

**Degraded triggers:**
1. Error rate ≥ half threshold
2. Average latency > threshold (default 30s)

### `core/budget.py`

**Enums:**
- `BudgetAction(ALLOW, WARN, DOWNGRADE, BLOCK)`

**Data classes:**
- `BudgetCheckResult(action, hourly_spend_usd, session_spend_usd, hourly_limit_usd, session_limit_usd, hourly_pct, session_pct, downgrade_to, reason)`

**Classes:**
- `BudgetEnforcer(config: BudgetConfig)` — in-memory spend tracker
  - `record_spend(cost_usd)` — called after each response
  - `get_hourly_spend() → float` — rolling 1-hour window
  - `session_spend → float` — cumulative session total
  - `check(requested_tier) → BudgetCheckResult`

**Action cascade:**
1. Under warning threshold → ALLOW
2. Above warning threshold (default 80%) → WARN (adds response headers)
3. Above limit + auto_downgrade + downgradeable tier → DOWNGRADE (COMPLEX→MEDIUM, MEDIUM→SIMPLE)
4. Above limit + no downgrade possible (already SIMPLE or auto_downgrade=false) → BLOCK (returns 429)

### Enhanced `core/arbitrage.py`

**ArbitrageResult** gains:
- `fallback_chain: list[ModelInfo]` — healthy alternatives sorted by cost, excluding the chosen model
- `skipped_unhealthy: list[str]` — providers skipped due to health status

**`select_cheapest()`** gains:
- `health_tracker: ProviderHealthTracker | None` — filters unhealthy providers
- When all providers are unhealthy, falls back to using all (avoids total failure)

### Enhanced `core/router.py`

**Router** gains constructor params:
- `health_tracker: ProviderHealthTracker | None`
- `budget_enforcer: BudgetEnforcer | None`

**Routing pipeline changes:**
1. Classify complexity (unchanged)
2. **Budget check** → may WARN, DOWNGRADE tier, or BLOCK
3. **Health-aware arbitrage** → skips unhealthy providers, builds fallback chain
4. **RoutingDecision** now includes `budget_action`, `budget_warning`, `fallback_chain`

### Enhanced `tracking/store.py`

New methods:
- `record_provider_health(provider, model, latency_ms, status_code, rate_limited=False, error=None)` — writes to `provider_health` table
- `record_budget_alert(alert_type, threshold_usd, current_usd, action_taken)` — writes to `budget_alerts` table
- `get_hourly_spend() → float` — SQL query for spend in last hour
- `get_session_spend(session_id) → float` — SQL query for session total
- `get_provider_health_stats(provider, window_minutes=5) → dict` — error/rate-limit counts from DB

---

## Proxy Request Pipeline (after Phase 3)

```
Request arrives at proxy
    │
    ├── 1. Parse request body (JSON)
    ├── 2. Get graph from cache (GraphCache.get())
    ├── 3. Extract signals (signals.py)
    │       └── If graph available: compute graph_complexity → inject into SignalBundle
    ├── 4. Classify complexity (classifier.py) → SIMPLE / MEDIUM / COMPLEX
    ├── 5. Budget check (budget.py)                     ← NEW IN PHASE 3
    │       ├── ALLOW: proceed normally
    │       ├── WARN: proceed, add warning headers
    │       ├── DOWNGRADE: reduce tier (COMPLEX→MEDIUM, MEDIUM→SIMPLE)
    │       └── BLOCK: return 429 with budget_exceeded error
    ├── 6. Route to optimal model (router.py + arbitrage.py)
    │       ├── Filters unhealthy providers             ← NEW IN PHASE 3
    │       └── Builds fallback chain                   ← NEW IN PHASE 3
    ├── 7. Prune context (pruner.py)
    ├── 8. Translate format if cross-provider (translator.py)
    ├── 9. Forward to provider API
    │       └── If 429: retry with fallback chain       ← NEW IN PHASE 3
    ├── 10. Record provider health                      ← NEW IN PHASE 3
    │       ├── Success → record_success
    │       ├── Error → record_error
    │       └── 429 → record_rate_limit
    ├── 11. Record spend to budget enforcer             ← NEW IN PHASE 3
    ├── 12. Record to SQLite
    └── 13. Return response with headers:
            x-costwise-routed: <model>
            x-costwise-tier: <SIMPLE|MEDIUM|COMPLEX>
            x-costwise-pruned: <tokens_saved>
            x-costwise-budget-action: <allow|warn|downgrade|block>  ← NEW
            x-costwise-budget-warning: <message>                     ← NEW
```

---

## Key Design Decisions

1. **In-memory health tracker, not DB-only.** The circuit breaker uses a deque-based sliding window in memory for O(1) health checks. The DB records are for historical analysis. The in-memory tracker is the source of truth for routing decisions.

2. **Cooldown-based rate limit handling.** When a provider returns 429, it's marked unhealthy for 30s (configurable). This prevents hammering a rate-limited provider. The cooldown is independent of the sliding window — even if the window evicts old events, the rate limit timestamp persists.

3. **Graceful degradation when all providers unhealthy.** If all providers in a tier are unhealthy, the arbiter falls back to using all of them (least-bad). This prevents total failure — better to try a rate-limited provider than refuse all requests.

4. **Budget downgrade cascade.** Budget enforcement uses a cascading strategy: WARN → DOWNGRADE → BLOCK. This keeps the agent working (just with cheaper models) rather than hard-stopping. COMPLEX→MEDIUM→SIMPLE progression gives two levels of downgrade before blocking.

5. **Fallback chain is pre-computed.** The routing decision includes a `fallback_chain` of model names. The proxy walks this chain on 429 without re-running arbitrage. Maximum 3 retries per request.

6. **Budget is in-memory + best-effort.** The BudgetEnforcer tracks spend in-memory for fast checks. It uses `record_spend()` after each response. On proxy restart, the in-memory state resets — this is intentional (session-scoped budget resets per session, hourly budget would need DB catch-up for stricter enforcement).

7. **Health and budget are optional.** The router works without either. If no health_tracker is passed, arbitrage doesn't filter by health. If no budget_enforcer is passed, all requests are ALLOW. Phase 3 features are purely additive.

---

## Config Reference

```toml
[costwise.budget]
max_hourly_usd = 10.0          # Rolling 1-hour spend cap (null = unlimited)
max_session_usd = 50.0         # Cumulative session cap (null = unlimited)
auto_downgrade = true           # Downgrade tier when budget exceeded (vs block)
warning_threshold_pct = 80      # Warn when spend reaches this % of limit
```

---

## Validation Results

| Metric | Target | Actual |
|--------|--------|--------|
| Test suite | All passing | 72/72 (0.40s) |
| Health: rate limit → unhealthy | Immediate | OK |
| Health: cooldown expires → healthy | After cooldown_s | OK (0.1s test) |
| Health: window eviction | Old events dropped | OK |
| Budget: warning at 80% | Correct action | OK |
| Budget: auto-downgrade | COMPLEX→MEDIUM→SIMPLE | OK |
| Budget: block at SIMPLE exceeded | Returns BLOCK | OK |
| Arbitrage: skips unhealthy | Filters correctly | OK |
| Arbitrage: all unhealthy → use all | Graceful fallback | OK |
| Fallback chain | Ordered by cost, excludes chosen | OK |

---

## What's Next: Phase 4 (Integration Layer)

Per `COSTWISE_PLAN.md`:

### Files to create:
```
src/costwise/integrations/
├── graphify.py            # Graph reader + optional MCP client
├── headroom.py            # CompressionHooks subclass + proxy chaining
├── rtk.py                 # SQLite reader for RTK's gain data
├── ponytail.py            # Detect Ponytail mode, expose as classifier signal
└── litellm.py             # LiteLLM callback adapter
```

### What already exists for Phase 4:
- `graph/` module reads Graphify's output — `integrations/graphify.py` adds MCP client
- Ponytail mode detection exists in `signals.py` — `integrations/ponytail.py` adds config reader
- RTK's SQLite schema is known — `integrations/rtk.py` reads its tracking DB
- Headroom proxy chaining architecture is defined in the plan

---

## Environment

- Python 3.14 venv at `.venv/`
- Dependencies: `uv pip install -e ".[proxy,dev]"`
- CLI: `costwise --version` → 0.1.0
- Tests: `.venv/bin/python -m pytest tests/ -v`
- Proxy version: 0.3.0
