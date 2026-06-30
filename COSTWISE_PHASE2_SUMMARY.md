# Costwise Phase 2 Summary: Graph-Guided Context Budget

> **Load this document + `COSTWISE_PLAN.md` at the start of a new session to resume with Phase 3.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Python venv: `.venv/` (Python 3.14, `uv pip install -e ".[proxy,dev]"`)

---

## What Phase 2 Built

Phase 2 adds **graph-guided context pruning** — using Graphify's knowledge graph to identify which context entries are relevant to the current task and drop the rest before forwarding to the LLM provider. This reduces input tokens sent, which compounds with routing savings from Phase 1.

**Cost equation so far:**
- Phase 1 (routing): sends task to a cheaper model → ~50% model cost savings
- Phase 2 (pruning): sends fewer tokens to that model → ~33-45% token savings
- Combined: fewer tokens × cheaper model = ~67-73% total savings

---

## Complete File Tree After Phase 2

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
│   └── schema.py            # Pydantic v2 — all config models (MODIFIED: +GraphConfig)
├── core/
│   ├── __init__.py
│   ├── models.py            # Tier, RoutingDecision, CostEstimate, ModelInfo, SignalBundle (MODIFIED: +graph_complexity)
│   ├── pricing.py           # 10 models pricing registry
│   ├── signals.py           # Extract 8 classification signals from request bodies
│   ├── classifier.py        # Weighted scorer → SIMPLE|MEDIUM|COMPLEX (MODIFIED: +graph signal w=0.15)
│   ├── arbitrage.py         # Cross-provider cheapest model selection
│   └── router.py            # Orchestrator: signals → classify → arbitrage (MODIFIED: +graph param)
├── graph/                   # ← ALL NEW IN PHASE 2
│   ├── __init__.py
│   ├── loader.py            # Parse Graphify's graph.json → CodeGraph
│   ├── relevance.py         # BFS relevance scoring + community boost + graph_complexity
│   ├── pruner.py            # Drop low-relevance context entries from messages
│   └── cache.py             # Thread-safe in-memory cache, reloads on file change
├── proxy/
│   ├── __init__.py
│   ├── health.py            # GET /health, GET /ready
│   ├── server.py            # v0.2.0 — classifies, routes, AND prunes every request (MODIFIED)
│   └── translator.py        # OpenAI ↔ Anthropic format translation
└── tracking/
    ├── __init__.py
    ├── schema.sql            # DDL (MODIFIED: +tokens_pruned, +messages_pruned columns)
    └── store.py              # SQLite WAL mode (MODIFIED: +tokens_pruned, +messages_pruned fields)

tests/
├── conftest.py              # Shared fixtures (httpx_graph_path, httpx_graph)
├── test_graph_loader.py     # 9 tests — node/edge parsing, indices, bidirectionality
├── test_graph_relevance.py  # 11 tests — reference extraction, BFS, community boost, complexity
├── test_graph_pruner.py     # 6 tests — pruning, protection, thresholds
└── test_graph_cache.py      # 8 tests — load, cache, invalidate, reload on change
```

---

## Module API Reference

### `graph/loader.py`

**Data classes:**
- `GraphNode(id, label, source_file, source_location, community, file_type)` — frozen, slotted
- `GraphEdge(source, target, relation, weight, confidence)` — frozen, slotted
- `CodeGraph` — in-memory graph with:
  - `nodes: dict[str, GraphNode]`
  - `adjacency: dict[str, list[tuple[str, GraphEdge]]]`
  - `file_to_nodes: dict[str, list[str]]` — file path → node IDs
  - `communities: dict[int, set[str]]` — community ID → node IDs
  - Methods: `neighbors(id)`, `nodes_for_file(path)` (suffix match), `community_of(id)`, `same_community(a, b)`

**Functions:**
- `load_graph(path) → CodeGraph` — parses Graphify's node-link JSON

**Constants:**
- `EDGE_WEIGHTS` — relation → weight: `imports_from(1.0) > inherits(0.95) > calls(0.85) > method(0.8) > contains(0.7) > uses(0.6) > semantically_similar_to(0.4)`

### `graph/relevance.py`

**Data classes:**
- `RelevanceResult(scores, seed_nodes, files_found)` — per-node relevance scores
  - Methods: `score_for_file(path, graph)`, `above_threshold(t)`

**Functions:**
- `score_relevance(graph, text, *, max_hops=4, decay=0.5, community_boost=0.2, min_edge_weight=0.0) → RelevanceResult`
  - Algorithm: extract refs → find seeds → BFS with decay × edge_weight → community boost
- `extract_references(text) → (files, symbols)` — regex extraction of file paths and CamelCase symbols
- `compute_graph_complexity(graph, text) → float` — 0-1 score based on avg degree of referenced nodes vs max degree in graph

### `graph/pruner.py`

**Data classes:**
- `PruneResult(original_messages, pruned_messages, original_token_estimate, pruned_token_estimate, dropped_entries)`
  - Properties: `tokens_saved`, `reduction_pct`

**Functions:**
- `prune_context(messages, graph, *, threshold=0.1, max_hops=4, decay=0.5, community_boost=0.2, protect_roles={"system"}, protect_last_n=2) → (pruned_messages, PruneResult)`
  - Scores relevance from LAST user message only (current intent)
  - Protects system prompts and last N messages (current turn)
  - Prunes at message level OR content-block level (multi-block messages)

### `graph/cache.py`

**Classes:**
- `GraphCache(graph_path=None)` — thread-safe
  - Properties: `is_available`, `load_error`
  - Methods: `get() → CodeGraph | None`, `configure(path)`, `invalidate()`, `clear()`
  - Checks file mtime every 2s, reloads on change

### `config/schema.py` — New `GraphConfig`

```python
class GraphConfig(BaseModel):
    enabled: bool = True
    graph_path: str = "graphify-out/graph.json"
    relevance_threshold: float = 0.1
    max_hops: int = 4
    decay: float = 0.5
    community_boost: float = 0.2
    protect_last_n: int = 2
```

TOML config section:
```toml
[costwise.graph]
enabled = true
graph_path = "graphify-out/graph.json"
relevance_threshold = 0.1
max_hops = 4
```

---

## Proxy Request Pipeline (after Phase 2)

```
Request arrives at proxy
    │
    ├── 1. Parse request body (JSON)
    ├── 2. Get graph from cache (GraphCache.get())
    ├── 3. Extract signals (signals.py)
    │       └── If graph available: compute graph_complexity → inject into SignalBundle
    ├── 4. Classify complexity (classifier.py) → SIMPLE / MEDIUM / COMPLEX
    │       └── graph signal weight = 0.15
    ├── 5. Route to optimal model (router.py + arbitrage.py)
    ├── 6. Prune context (pruner.py)          ← NEW IN PHASE 2
    │       ├── Score relevance from last user message
    │       ├── Drop messages below threshold
    │       └── Protect system prompt + last N messages
    ├── 7. Translate format if cross-provider (translator.py)
    ├── 8. Forward to provider API
    ├── 9. Record to SQLite (tokens_pruned, messages_pruned)
    └── 10. Return response with headers:
            x-costwise-routed: <model>
            x-costwise-tier: <SIMPLE|MEDIUM|COMPLEX>
            x-costwise-pruned: <tokens_saved>   ← NEW IN PHASE 2
```

**Key design: pruning happens AFTER classification but BEFORE forwarding.** The classifier needs full context for accurate complexity scoring. The pruner reduces what gets sent to the provider.

---

## Key Design Decisions

1. **Pure-Python graph — no NetworkX at runtime.** `CodeGraph` uses adjacency dicts + indices. NetworkX stays optional in `[graph]` extra for anyone who wants heavier analysis. Keeps the install lightweight.

2. **Focus-based relevance.** The pruner scores relevance from the LAST user message only (the current intent), not the entire conversation history. This prevents every file ever mentioned in the conversation from becoming a seed node, which would defeat the purpose of pruning.

3. **Edge-type weighting in BFS.** Not all graph edges are equal. An `imports_from` edge propagates full relevance (1.0), while `semantically_similar_to` only propagates 0.4. This means tightly coupled code stays in context while loosely related code gets pruned.

4. **Community boost is additive (+0.2).** Nodes in the same Leiden community as seed nodes get a relevance bump even if BFS hasn't reached them. This catches architecturally-nearby code that happens to be graph-distant (e.g., utility functions in the same module).

5. **Content-block-level pruning.** When a message has multiple content blocks (common in tool results showing several files), the pruner can drop individual blocks rather than the entire message. Finer granularity = better results.

6. **File path suffix matching.** `nodes_for_file("auth.py")` matches `"worked/httpx/raw/auth.py"`. Tolerant of path differences between how prompts reference files vs how Graphify stores them.

7. **Graceful degradation.** No graph file → feature disabled, zero behavior change from Phase 1. Invalid graph file → logged warning, feature disabled. This means Costwise works without Graphify installed.

---

## Classifier Signal Weights (Updated)

| Signal | Weight | Fires when |
|--------|--------|------------|
| error | 0.18 | Error keywords in messages |
| retry | 0.18 | Retry keywords present |
| graph | **0.15** | **Files reference central nodes in knowledge graph** ← NEW |
| code+tools | 0.15 | Both code AND tools present |
| tools | 0.12 | Tools array non-empty |
| code | 0.12 | Code patterns found |
| tokens | 0.10 | Token count 500–10K (normalized) |
| depth | 0.08 | Conversation >2 messages |
| images | 0.07 | Image blocks in messages |

Ponytail biases: ultra=-0.15, full=-0.08, lite=-0.03

---

## Tracking Schema (Updated)

```sql
-- New columns added to routing_decisions:
tokens_pruned   INTEGER,   -- estimated tokens removed by graph pruning
messages_pruned INTEGER    -- number of messages/blocks dropped
```

`RoutingRecord` dataclass gains: `tokens_pruned: int | None`, `messages_pruned: int | None`

---

## Validation Results

| Metric | Target | Actual |
|--------|--------|--------|
| Test suite | All passing | 35/35 (0.04s) |
| Graph load (144 nodes, 330 edges) | Works | OK |
| Pruning: auth focus, 6-file context | Drops irrelevant | 8→5 msgs, 45% reduction |
| Large context pruning | Meaningful reduction | 12K→8K tokens, 33% reduction |
| Graph operations latency | <5ms | 2.26ms |
| Graceful fallback (no graph) | No error | Feature disabled silently |

---

## What's Next: Phase 3 (Provider Arbitrage + Budget Policies)

Per `COSTWISE_PLAN.md`:

### Enhancements to existing files:
- **`core/arbitrage.py`** — Rank equivalent models by $/MTok across providers, select cheapest available
- **`core/router.py`** — Add budget enforcement: hourly/session spend limits, auto-downgrade when budget exceeded
- **`tracking/store.py`** — Provider health tracking: rate limit events, error rates, latency percentiles
- **Fallback chains** — Rate limit on primary provider → automatically try next cheapest equivalent model

### Budget policy config (already in schema, needs enforcement):
```toml
[costwise.budget]
max_hourly_usd = 10.0
max_session_usd = 50.0
auto_downgrade = true
warning_threshold_pct = 80
```

### What already exists for Phase 3:
- `arbitrage.py` has `select_cheapest()` — needs provider health filtering
- `BudgetConfig` exists in schema — needs enforcement logic in router
- `provider_health` table exists in schema.sql — needs insert logic
- `budget_alerts` table exists in schema.sql — needs alert logic

---

## Environment

- Python 3.14 venv at `.venv/`
- Dependencies: `uv pip install -e ".[proxy,dev]"`
- CLI: `costwise --version` → 0.1.0
- Tests: `.venv/bin/python -m pytest tests/ -v`
- Graph fixture: uses `~/Desktop/graphify/worked/httpx/graph.json` (real Graphify output)
