# Costwise Phase 6 Summary: Quality Feedback Loop

> **Load this document + `COSTWISE_PLAN.md` at the start of a new session to resume with Phase 7.**
> Project directory: `/Users/goron/Desktop/the_next_big_thing`
> Python venv: `.venv/` (Python 3.14, `uv pip install -e ".[proxy,dashboard,mcp,dev]"`)

---

## What Phase 6 Built

Phase 6 closes the routing accuracy loop: detect retries, record classification errors, auto-tune thresholds, and track false-downgrade rate (target <3%). Costwise is now a self-improving system.

**Cost equation so far:**
- Phase 1 (routing): cheaper model → ~50% model cost savings
- Phase 2 (pruning): fewer tokens → ~33-45% token savings
- Phase 3 (arbitrage + budget): cheapest *healthy* provider + spend caps
- Phase 4 (integrations): Headroom compression + RTK shell savings + Ponytail output reduction
- Phase 5 (visibility): dashboard for monitoring, MCP for AI-native access, doctor for diagnostics
- Phase 6 (feedback): retry detection → auto-tune thresholds → quality grade tracking
- Combined: RTK filters → Costwise routes + prunes + self-corrects → Headroom compresses → cheapest provider → dashboard tracks it all

---

## Complete File Tree After Phase 6

```
src/costwise/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── main.py
│   ├── gain_cmd.py
│   ├── doctor_cmd.py
│   └── wrap_cmd.py
├── config/
│   ├── __init__.py
│   ├── loader.py
│   └── schema.py              # MODIFIED: +FeedbackConfig
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
├── dashboard/
│   ├── __init__.py
│   ├── app.py                 # MODIFIED: +/api/feedback, +/partials/feedback, +quality_gauge in context
│   ├── data.py                # MODIFIED: +feedback_summary field
│   ├── charts.py              # MODIFIED: +quality_gauge() SVG function
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html     # MODIFIED: +feedback panel
│   │   └── partials/
│   │       ├── requests.html
│   │       ├── costs.html
│   │       ├── models.html
│   │       ├── savings.html
│   │       ├── budget.html
│   │       └── feedback.html  # ← NEW
│   └── static/
│       ├── htmx.min.js
│       └── style.css
├── feedback/                   # ← ALL NEW
│   ├── __init__.py
│   ├── fingerprint.py         # Content hashing + similarity
│   ├── detector.py            # RetryDetector — flags retries
│   ├── tuner.py               # ThresholdTuner — bounded auto-adjustment
│   └── metrics.py             # FeedbackMetrics — quality grade (A-F)
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
├── mcp/
│   ├── __init__.py
│   ├── server.py              # MODIFIED: +costwise_feedback tool (5th tool)
│   └── __main__.py
├── proxy/
│   ├── __init__.py
│   ├── health.py
│   ├── server.py              # MODIFIED: +fingerprinting, +retry detection, +tier override, +tuner
│   └── translator.py
└── tracking/
    ├── __init__.py
    ├── schema.sql              # MODIFIED: +retry_events, +threshold_adjustments tables
    └── store.py                # MODIFIED: +content_hash column, +7 new query methods, +_migrate()

tests/
├── conftest.py
├── test_graph_loader.py             #  9 tests
├── test_graph_relevance.py          # 11 tests
├── test_graph_pruner.py             #  6 tests
├── test_graph_cache.py              #  8 tests
├── test_health.py                   # 16 tests
├── test_budget.py                   # 11 tests
├── test_arbitrage_health.py         # 11 tests
├── test_integration_ponytail.py     # 15 tests
├── test_integration_rtk.py          # 10 tests
├── test_integration_headroom.py     # 11 tests
├── test_integration_graphify.py     #  9 tests
├── test_integration_litellm.py      # 11 tests
├── test_dashboard_data.py           # 14 tests
├── test_dashboard_charts.py         # 13 tests
├── test_dashboard_app.py            # 11 tests
├── test_mcp_server.py               # 12 tests
├── test_cli_commands.py             # 20 tests
├── test_feedback_fingerprint.py     # 23 tests ← NEW
├── test_feedback_detector.py        # 10 tests ← NEW
├── test_feedback_tuner.py           # 13 tests ← NEW
└── test_feedback_metrics.py         # 12 tests ← NEW
```

---

## Module API Reference

### `feedback/fingerprint.py`

Content hashing and similarity for retry detection.

**Functions:**
- `fingerprint(messages: list[dict]) -> str` — SHA-256 hash of the normalized last user message
- `similarity(messages_a, messages_b) -> float` — two-tier: exact hash match (1.0) or word-set Jaccard (0.0–1.0)

**Internal helpers:**
- `_extract_user_text(messages)` — extracts text from the last `role: "user"` message, handles both string and content-block arrays
- `_normalize(text)` — lowercase, strip punctuation, collapse whitespace
- `_word_set(text)` — set of words from normalized text

### `feedback/detector.py`

Retry detection by comparing incoming requests against recent routing decisions.

**Data classes:**
- `RetryEvent(session_id, original_request_id, content_hash, similarity_score, original_tier, original_model, time_delta_s, was_downgraded)` — frozen

**Classes:**
- `RetryDetector(store, window_minutes=5, similarity_threshold=0.7)`
  - `async check(session_id, messages, content_hash=None) -> RetryEvent | None`

**Detection algorithm:**
1. Hash incoming messages
2. Query `get_recent_fingerprints()` for same session within window
3. Compare: exact hash match → 1.0; otherwise 4-gram Jaccard on hex chars
4. If similarity ≥ threshold and original was downgraded → `RetryEvent`

### `feedback/tuner.py`

Bounded auto-adjustment of classifier thresholds based on retry feedback. Thread-safe via `threading.Lock`.

**Data classes:**
- `NudgeRecord(timestamp, field, old_value, new_value)`

**Classes:**
- `ThresholdTuner(classifier_config, feedback_config, store)`
  - `async on_retry(event: RetryEvent) -> bool` — nudge threshold down on retry (tighten)
  - `async maybe_relax() -> bool` — nudge thresholds up when quality is excellent
  - `record_request()` — increment request counter
  - `nudge_count_this_hour: int` — property

**Auto-tuning algorithm:**
- On SIMPLE retry → lower `simple_threshold` by `nudge_step` (0.01)
- On MEDIUM retry → lower `complex_threshold` by `nudge_step` (0.01)
- Bounds: simple ∈ [0.05, 0.40], complex ∈ [0.35, 0.80]
- Gap constraint: `complex - simple ≥ 0.15` (uses `- 1e-9` epsilon for float precision)
- Rate limit: max 5 nudges per hour
- Cold-start guard: min 20 requests before any tuning
- Relaxation: if false-downgrade rate < target × 0.3, nudge UP by `nudge_step / 2`

### `feedback/metrics.py`

**Functions:**
- `quality_grade(false_downgrade_rate: float) -> str` — A (<1%), B (<2%), C (<3%), D (<5%), F (≥5%)

**Classes:**
- `FeedbackMetrics(store)`
  - `async get_summary(window_minutes=60, current_simple_threshold=None, current_complex_threshold=None) -> dict`

**Summary dict keys:**
`retry_rate`, `retry_count`, `false_downgrade_rate`, `false_downgrade_count`, `total_requests`, `total_downgrades`, `quality_grade`, `total_threshold_adjustments`, `recent_adjustments`, `window_minutes`, `current_simple_threshold`, `current_complex_threshold`

---

## Schema Changes (Phase 6)

Two new tables in `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS retry_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    session_id           TEXT NOT NULL,
    original_request_id  INTEGER NOT NULL,
    retry_request_id     INTEGER NOT NULL,
    content_hash         TEXT NOT NULL,
    similarity_score     REAL NOT NULL,
    original_tier        TEXT NOT NULL,
    original_model       TEXT NOT NULL,
    time_delta_s         REAL NOT NULL,
    was_downgraded       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS threshold_adjustments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    field               TEXT NOT NULL,
    old_value           REAL NOT NULL,
    new_value           REAL NOT NULL,
    reason              TEXT NOT NULL,
    retry_event_id      INTEGER,
    window_retry_rate   REAL,
    window_requests     INTEGER
);
```

Migration in `store.py._migrate()`:
```python
ALTER TABLE routing_decisions ADD COLUMN content_hash TEXT
```
Plus `CREATE INDEX IF NOT EXISTS idx_routing_content_hash ON routing_decisions(session_id, content_hash)` — both wrapped in try/except for idempotent re-runs.

---

## New TrackingStore Methods (Phase 6)

```python
async def record_retry_event(session_id, original_request_id, retry_request_id,
    content_hash, similarity_score, original_tier, original_model,
    time_delta_s, was_downgraded) -> None

async def record_threshold_adjustment(field, old_value, new_value, reason,
    retry_event_id=None, window_retry_rate=None, window_requests=None) -> None

async def get_recent_fingerprints(session_id, window_minutes=5) -> list[dict]

async def get_retry_rate(window_minutes=60) -> dict  # {retry_rate, retry_count, total_requests}

async def get_false_downgrade_rate(window_minutes=60) -> dict  # {false_downgrade_rate, false_downgrade_count, total_downgrades}

async def get_threshold_history(limit=50) -> list[dict]

async def get_feedback_summary() -> dict  # {total_requests, total_retries, total_threshold_adjustments, false_downgrade_rate}
```

---

## Config Changes (Phase 6)

New `FeedbackConfig` in `config/schema.py`:

```python
class FeedbackConfig(BaseModel):
    enabled: bool = True
    retry_window_minutes: int = 5
    similarity_threshold: float = 0.7
    auto_tune: bool = True
    nudge_step: float = 0.01
    simple_threshold_min: float = 0.05
    simple_threshold_max: float = 0.40
    complex_threshold_min: float = 0.35
    complex_threshold_max: float = 0.80
    min_threshold_gap: float = 0.15
    min_requests_for_tuning: int = 20
    max_nudges_per_hour: int = 5
    target_false_downgrade_rate: float = 0.03
```

Added as `feedback: FeedbackConfig` on `CostwiseConfig` (between `graph` and `integrations`).

TOML config:
```toml
[costwise.feedback]
enabled = true
auto_tune = true
nudge_step = 0.01
target_false_downgrade_rate = 0.03
```

---

## Proxy Integration (Phase 6)

The request pipeline in `proxy/server.py` now includes:

```
Request arrives
  ├─ 1. fingerprint(messages) → content_hash
  ├─ 2. detector.check(session_id, content_hash) → RetryEvent | None
  ├─ 3. If retry detected: override tier (SIMPLE→MEDIUM, MEDIUM→COMPLEX)
  │     Reset routed_model to original_model
  ├─ 4. Route (with possibly overridden tier)
  ├─ 5. Forward to upstream, get response
  ├─ 6. Record routing_decision (now includes content_hash)
  └─ 7. If retry: record retry_event, feed tuner.on_retry()
```

Key variables in `create_app()`:
- `_TIER_UPGRADE = {Tier.SIMPLE: Tier.MEDIUM, Tier.MEDIUM: Tier.COMPLEX}`
- `retry_detector: RetryDetector | None` — created if `config.feedback.enabled`
- `tuner: ThresholdTuner | None` — created if `config.feedback.enabled`

Both the non-streaming and streaming response paths handle retry recording.

---

## Dashboard Changes (Phase 6)

**New endpoints:**
| Route | Method | Response | Purpose |
|-------|--------|----------|---------|
| `/api/feedback` | GET | JSON | Feedback summary (retry rate, quality grade) |
| `/partials/feedback` | GET | HTML | HTMX partial: quality gauge SVG + stats |

**New chart:**
- `quality_gauge(false_downgrade_pct, target_pct=3.0, width=200, height=120) -> str` — half-circle SVG arc gauge with letter grade (A–F), colored by grade

**Template changes:**
- `dashboard.html` — added "Routing Quality" panel with `hx-trigger="every 5s"`
- `partials/feedback.html` — gauge + stats grid (grade, false-downgrade rate, retry rate, window requests)

**Data changes:**
- `DashboardData` — added `feedback_summary: dict[str, Any]`
- `_build_template_context()` — added `quality_chart` and `feedback`
- `_serialize_dashboard()` — added `feedback_summary`

---

## MCP Changes (Phase 6)

**New tool (5th):**
| Tool | Args | Returns |
|------|------|---------|
| `costwise_feedback` | `window_minutes: int = 60` | JSON: retry_rate, false_downgrade_rate, quality_grade, threshold history |

---

## Key Design Decisions (Phase 6)

1. **Two-tier similarity.** Exact content-hash match catches identical retries (free). Word-set Jaccard catches rephrased retries (cheap). No embeddings needed — keeps it zero-infra.

2. **Bounded tuning with gap constraint.** Thresholds can only move ±0.01 per event, bounded to [0.05, 0.80], with a 0.15 minimum gap between simple and complex. The MEDIUM band can never collapse.

3. **Rate-limited nudges.** Max 5 per hour prevents runaway oscillation from burst retries.

4. **Cold-start guard.** No tuning until 20 requests observed — avoids reacting to noise.

5. **Relaxation when quality is excellent.** If false-downgrade rate < 1% (target × 0.3), thresholds relax by half-step to reclaim savings. Self-balancing: tightens on errors, loosens on success.

6. **Thread-safe tuner.** `ThresholdTuner` mutates `ClassifierConfig` in-place and uses `threading.Lock` because the proxy is async with `to_thread` DB calls.

7. **Float epsilon in gap checks.** All gap comparisons use `- 1e-9` to handle IEEE 754 precision (e.g., `0.35 - 0.20 = 0.14999999999999997`).

8. **Migration is idempotent.** `ALTER TABLE ADD COLUMN` wrapped in try/except for duplicate column errors. Index creation uses `IF NOT EXISTS` inside `_migrate()` (not in `schema.sql`, since the column doesn't exist until ALTER TABLE runs).

---

## Validation Results

| Metric | Target | Actual |
|--------|--------|--------|
| Test suite | All passing | 256/256 (1.06s) |
| Original tests (Phases 0-5) | No regressions | 198/198 pass |
| New Phase 6 tests | All passing | 58/58 pass |
| Fingerprint: normalization | Deterministic hash | OK |
| Fingerprint: multi-format | String + content-block messages | OK |
| Fingerprint: empty messages | Returns hash of empty string | OK |
| Similarity: exact match | Returns 1.0 | OK |
| Similarity: disjoint content | Returns 0.0 | OK |
| Similarity: partial overlap | Returns Jaccard coefficient | OK |
| Detector: retry on downgraded request | Returns RetryEvent | OK |
| Detector: no retry on non-downgraded | Returns None | OK |
| Detector: window expiry | Returns None after window | OK |
| Tuner: SIMPLE retry → lower simple_threshold | Nudges by 0.01 | OK |
| Tuner: MEDIUM retry → lower complex_threshold | Nudges by 0.01 | OK |
| Tuner: bounds respected | Won't go below min | OK |
| Tuner: gap constraint | Won't violate 0.15 gap | OK |
| Tuner: rate limit | Blocks after 5/hour | OK |
| Tuner: cold-start guard | Blocks before 20 requests | OK |
| Tuner: relaxation | Nudges up when rate < target×0.3 | OK |
| Metrics: quality grade | A/B/C/D/F mapping correct | OK |
| Metrics: summary | Aggregates all data sources | OK |
| Dashboard: /api/feedback | Returns JSON | OK |
| Dashboard: /partials/feedback | Returns HTML with SVG | OK |
| Dashboard: quality panel | Renders in grid | OK |
| MCP: costwise_feedback | Returns quality metrics JSON | OK |

---

## What's Next: Phase 7

The `COSTWISE_PLAN.md` defines 6 phases (0-6), all now complete. Phase 7 is undefined — possible directions:

1. **ML-based routing** — RouteLLM matrix-factorization classifier for learned routing (mentioned as optional in Phase 6)
2. **Validation suite** — replay 10 real coding sessions under 6 configs (baseline, RTK-only, Headroom-only, Ponytail-only, Costwise-only, full stack) per the plan's validation section
3. **PyO3 Rust hot-path** — move classifier to Rust for <0.1ms p99 classification (mentioned in tech stack)
4. **Multi-user support** — per-user budgets, API key management, shared dashboard
5. **CI/CD integration** — GitHub Action for cost-aware PR reviews
6. **Publishing** — PyPI package, docs site, README, demo video

---

## Environment

- Python 3.14 venv at `.venv/`
- Dependencies: `uv pip install -e ".[proxy,dashboard,mcp,dev]"`
- CLI: `costwise --version` → 0.1.0
- Tests: `.venv/bin/python -m pytest tests/ -v`
- Dashboard: `costwise dashboard` → starts on :8789
- MCP: `python -m costwise.mcp` → stdio server
- Proxy: `costwise proxy` → starts on :8788
- All 256 tests pass as of 2026-06-30
