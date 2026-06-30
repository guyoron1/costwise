# Costwise: Agentic Cost Intelligence — Execution Plan

> **Load this document at the start of a new session to resume work.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Source repos (cloned locally): Graphify (`~/Desktop/graphify`), Headroom (`~/Desktop/headroom`), RTK (`~/Desktop/rtk`)
> External integration: Ponytail (`github.com/DietrichGebert/ponytail`) — output token reduction via agent behavior shaping

---

## Project Summary

**Costwise** is an open-source (Apache 2.0) Python package that provides intelligent model routing, graph-guided context budgeting, and provider arbitrage for AI coding agents. It is the orchestration layer connecting four complementary tools:

- **Graphify** — knowledge graph builder for codebases (YC S26)
- **Headroom** — token compression layer (60-95% savings on input tokens)
- **RTK** — CLI output filtering proxy (60-90% savings on input tokens, Rust)
- **Ponytail** — agent behavior shaping (54% reduction in output tokens, MIT, 68K stars)

**The insight:** The current ecosystem exclusively optimizes *input tokens*. But output tokens cost 2-5x more per token (Claude Opus: $15/MTok in vs $75/MTok out). Ponytail is the only tool that reduces output — the most expensive component. Routing multiplies with compression AND output reduction: fewer input tokens x cheaper model x fewer output tokens = compound savings.

**Predicted impact:** 95-97% total cost reduction when fully stacked (RTK + Ponytail + Costwise + Headroom).

## Key Decisions Made

- **Open-source, Apache 2.0 license** — same as RTK, designed for virality
- **Zero infrastructure cost** — runs entirely local, no cloud, no SaaS, no API keys needed for Costwise itself. Users bring their own LLM provider keys.
- **Zero maintenance cost** — only recurring work is updating a bundled pricing JSON when providers change prices (~5 min PR, community can contribute)
- **Data never leaves the user's machine** — local proxy, local SQLite, local dashboard

---

## Architecture: 4-Layer Stack

```
Agent CLI command    → RTK       (filter CLI output,      input  -70%)
Agent philosophy     → Ponytail  (reduce code generation,  output -54%)
Agent composes call  → Costwise  (route + prune context,   input  -50%, routing -50%)
LLM call dispatched  → Headroom  (compress tokens,         input  -60%)
Final call           → cheapest adequate provider API
```

> **Layer distinction:** RTK, Costwise, and Headroom are proxy/middleware layers that intercept requests. Ponytail is a *behavioral* layer — it shapes agent behavior via system prompt injection. Costwise reads Ponytail's state as a classifier signal but never controls it.

---

## Phase 0: Foundation (Week 1)

**Goal:** Passthrough proxy that can track every LLM call.

### Files to create:
```
src/costwise/
├── __init__.py
├── config/
│   ├── loader.py          # TOML config with env var interpolation
│   └── schema.py          # Pydantic models for config validation
├── tracking/
│   ├── store.py           # SQLite: insert routing decisions, query stats
│   └── schema.sql         # DDL: routing_decisions, provider_health, budget_alerts
├── proxy/
│   ├── server.py          # FastAPI ASGI proxy — forward all requests unchanged
│   └── health.py          # GET /health, GET /ready
└── cli/
    ├── main.py            # Click entrypoint: `costwise proxy`, `costwise gain`
    └── gain_cmd.py        # Token usage summary from SQLite
```

### Key decisions:
- **FastAPI + httpx** for proxy (async, streaming SSE support)
- **SQLite** at `~/.local/share/costwise/costwise.db` (XDG, like RTK)
- **Pydantic v2** for config validation
- **pyproject.toml** with uv support, optional extras: `[proxy]`, `[graph]`, `[ml]`, `[headroom]`

### Validation:
- Proxy passes requests through to upstream unchanged
- `costwise gain` shows raw token counts per session

---

## Phase 1: Classifier + Router (Weeks 2-3)

**Goal:** Route each LLM call to the optimal model based on complexity.

### Files to create:
```
src/costwise/core/
├── signals.py             # Extract: token count, tools, code presence, depth
├── classifier.py          # Rule-based weighted scorer → SIMPLE|MEDIUM|COMPLEX
├── router.py              # Tier + budget + health → RoutingDecision
├── pricing.py             # Bundled model pricing registry (JSON)
├── arbitrage.py           # Cross-provider comparison for equivalent tiers
└── models.py              # Pydantic: RoutingDecision, CostEstimate, Tier
```

### Classification signals (from request body):
| Signal | Weight | Source |
|--------|--------|--------|
| Tool being invoked | High | `tools` / `tool_choice` field |
| Prompt token count | Medium | Message array |
| Code in user message | Medium | Regex + heuristics |
| Conversation depth | Low | Message array length |
| Error/retry context | High | "retry", "error", "failed" keywords |
| Graph centrality of files | High | Graphify integration (Phase 2) |
| Ponytail active mode | Medium-High | `~/.config/ponytail/config.json` (ultra → bias SIMPLE) |

### Tier mapping:
- **SIMPLE** → Haiku/Flash/nano: file reads, search results, confirmations
- **MEDIUM** → Sonnet/4o: single-file edits, explanations, test writing
- **COMPLEX** → Opus/GPT-5: multi-file refactors, architecture, debugging chains

### Files to modify:
- `proxy/server.py` — wire classifier + router into request pipeline
- Add `proxy/translator.py` — OpenAI ↔ Anthropic format translation
- Add `proxy/streaming.py` — SSE passthrough across providers

### Validation:
- Hand-label 50 recorded requests → verify >80% classification accuracy
- Measure classification latency: target <1ms p99

---

## Phase 2: Graph-Guided Context Budget (Weeks 4-5)

**Goal:** Use Graphify's knowledge graph to prune irrelevant context.

### Files to create:
```
src/costwise/graph/
├── loader.py              # Parse Graphify's graph.json (NetworkX node-link format)
├── relevance.py           # BFS relevance scoring with community awareness
├── pruner.py              # Remove low-relevance context entries
└── cache.py               # In-memory graph cache, reload on file change
```

### How it works:
1. Extract file paths + symbols referenced in the current prompt
2. Walk graph edges from referenced nodes (BFS, decay=0.5 per hop)
3. Boost nodes in same Leiden community (+0.2 relevance)
4. Weight by edge type: `imports` > `calls` > `semantically_similar_to`
5. Prune context entries below threshold to fit token budget

### Integration with Graphify:
- Reads `graphify-out/graph.json` — Graphify's standard output
- Uses community assignments, god-node flags, confidence tags
- Falls back gracefully if no graph found (feature disabled, not an error)

### Validation:
- 50,000-token context → prune to 15,000 → task completion rate >95%
- Graph operations <5ms per request

---

## Phase 3: Provider Arbitrage + Budget Policies (Week 6)

**Goal:** Cross-provider cost optimization and spend controls.

### Enhancements:
- `core/arbitrage.py` — rank equivalent models by $/MTok, select cheapest
- `core/router.py` — add budget enforcement (hourly/session limits, auto-downgrade)
- `tracking/store.py` — provider health tracking (rate limits, errors, latency)
- Fallback chains: rate limit on primary → try next cheapest equivalent

### Budget policy config (in `costwise.toml`):
```toml
[costwise.budget]
max_hourly_usd = 10.0
max_session_usd = 50.0
auto_downgrade = true
warning_threshold_pct = 80
```

---

## Phase 4: Integration Layer (Weeks 7-8)

**Goal:** Connect to Graphify, Headroom, and RTK ecosystems.

### Files to create:
```
src/costwise/integrations/
├── graphify.py            # Graph reader + optional MCP client
├── headroom.py            # CompressionHooks subclass + proxy chaining
├── rtk.py                 # SQLite reader for RTK's gain data
├── ponytail.py            # Detect Ponytail mode, expose as classifier signal
└── litellm.py             # LiteLLM callback adapter
```

### Integration details:

**Graphify** (local: `~/Desktop/graphify/graphify/serve.py`):
- Read `graph.json` directly (primary)
- Optionally call Graphify's MCP `query_graph` tool for dynamic queries

**Headroom** (local: `~/Desktop/headroom/headroom/compress.py`):
- Subclass `CompressionHooks` for in-process integration
- Or chain proxies: Costwise (port 8788) → Headroom (port 8787) → provider
- Costwise prunes FIRST, Headroom compresses SECOND (maximize savings)

**RTK** (local: `~/Desktop/rtk/`, tracking DB at `~/.local/share/rtk/tracking.db`):
- Read RTK's SQLite tracking DB to merge shell-level savings into dashboard
- Unified savings view: RTK + Costwise + Headroom + Ponytail combined

**Ponytail** (external: `github.com/DietrichGebert/ponytail`):
- Read `~/.config/ponytail/config.json` to detect active mode (lite/full/ultra/off)
- Expose mode as classifier signal in `core/signals.py` (ultra → strong SIMPLE bias)
- Track output token reduction in dashboard when Ponytail is active
- Ponytail-aware arbitrage: when output is minimized, favor models with cheap input pricing
- Graceful fallback: feature disabled if Ponytail not installed (no error)

---

## Phase 5: Dashboard + MCP Server (Weeks 9-10)

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

### MCP tools:
| Tool | Purpose |
|------|---------|
| `costwise_route` | Classify prompt, return recommended model |
| `costwise_budget` | Graph-guided context budget for given files |
| `costwise_stats` | Session cost, savings, model distribution |
| `costwise_gain` | Cumulative savings summary (all 4 layers) |

---

## Phase 6: Quality Feedback Loop (Weeks 11-12)

**Goal:** Self-improving routing accuracy.

- Detect retries (same conversation hash + similar content within time window)
- Record retries as classification errors → auto-tune thresholds
- Track false-downgrade rate (COMPLEX task sent to cheap model) — target <3%
- Optional: integrate RouteLLM matrix-factorization classifier for ML-based routing

---

## What Makes This Novel

1. **Graph-guided context budgeting is new.** Nobody uses graph topology to decide which tokens to keep.
2. **Routing multiplies with compression.** 50% fewer tokens x 50% cheaper model = 75% savings (vs 50% from either alone).
3. **The feedback loop learns from failures.** Retries auto-tune classification thresholds.
4. **Provider arbitrage across equivalent tiers.** LiteLLM routes but doesn't classify complexity. RouteLLM classifies but doesn't arbitrage. Costwise does both.
5. **The four-tool stack story.** RTK + Ponytail + Costwise + Headroom = shell filtering + output reduction + model routing + context compression. No tool does all four.
6. **Output token awareness is the missing dimension.** Every existing cost tool optimizes input tokens. Output tokens cost 2-5x more per token. Ponytail integration makes Costwise the first tool to optimize both sides of the token equation.

---

## Validation: How to Prove the Prediction

Record 10 real coding sessions. Replay under 6 configs:

| Config | Expected cost vs baseline |
|--------|--------------------------|
| Baseline (Opus for everything) | 100% |
| RTK only | 70-80% (input savings, same model) |
| Headroom only | 60-80% (compression, same model) |
| Ponytail only | 65-80% (output reduction, same model) |
| Costwise routing only | 40-60% (cheaper models, same tokens) |
| Costwise + Graph | 30-50% (cheaper models + pruned context) |
| 3-tool stack (RTK + Costwise + Headroom) | 5-15% of baseline |
| Full stack (RTK + Ponytail + Costwise + Headroom) | 3-8% of baseline |

**Success criteria:** Full 4-tool stack achieves <10% of baseline cost with >95% task completion rate.

---

## Tech Stack Summary

- **Language:** Python 3.10+
- **License:** Apache 2.0 (open-source)
- **Proxy:** FastAPI + httpx (async, SSE streaming)
- **Config:** TOML + Pydantic v2
- **Tracking:** SQLite (XDG paths)
- **Graph:** NetworkX (read Graphify's output)
- **MCP:** FastMCP
- **Dashboard:** HTMX + Jinja2 (lightweight, no JS framework)
- **CLI:** Click
- **Optional Rust:** PyO3 classifier hot-path (Phase 6+)
- **Infrastructure cost:** $0 (fully local, no cloud services)
- **Maintenance cost:** $0 (community-maintained pricing JSON updates)

---

## How to Resume in a New Session

Tell Claude:
> Read the file `COSTWISE_PLAN.md` in this project directory. This is the execution plan for Costwise. Resume from where we left off — start with Phase 0 if no code exists yet.
