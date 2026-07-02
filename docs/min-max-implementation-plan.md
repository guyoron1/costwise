# Costwise Min-Max Routing: Implementation Plan

## The Problem

Costwise routes AI tasks to models by: extracting structural signals → scoring complexity (0.0–1.0) → assigning a tier (SIMPLE/MEDIUM/COMPLEX) → picking the cheapest healthy model in that tier.

Two fundamental blind spots prevent optimal cost/quality balance:

1. **The system doesn't account for the cost of being wrong.** If a cheap model fails and the user retries with an expensive model, total cost = cheap_attempt + expensive_retry > just_using_expensive_model. The arbitrage step (`select_cheapest` in `arbitrage.py`) minimizes `model_cost` when it should minimize `expected_total_cost = model_cost + P(retry) × retry_penalty`.

2. **Signals are structural, not semantic.** "Explain this function" and "Refactor this function across 3 files" both trigger `has_code=True`. Error signals are binary (a lint warning = a production crash). Intent — the single strongest complexity predictor — is not extracted.

---

## Architecture Overview (Current State)

```
Request → extract_signals() → classify() → router.route() → select_cheapest() → forward
                                                                  ↑
                                                          cheapest model in tier
```

### Key Files

| File | Role |
|------|------|
| `src/costwise/core/signals.py` | Extracts 12 structural signals from request bodies |
| `src/costwise/core/classifier.py` | Weighted scoring → tier assignment (SIMPLE < 0.20, MEDIUM < 0.55, COMPLEX ≥ 0.55) |
| `src/costwise/core/router.py` | Orchestrates: signals → classifier → budget check → arbitrage → RoutingDecision |
| `src/costwise/core/arbitrage.py` | Cross-provider arbitrage: finds cheapest healthy model for a tier |
| `src/costwise/core/models.py` | Data models: SignalBundle, RoutingDecision, ModelInfo, CostEstimate, Tier |
| `src/costwise/core/pricing.py` | Model pricing registry (per-MTok costs, capabilities, tiers) |
| `src/costwise/tracking/store.py` | SQLite tracking: routing decisions, retries, threshold adjustments |
| `src/costwise/tracking/schema.sql` | DB schema: routing_decisions, retry_events, threshold_adjustments, provider_health, budget_alerts |
| `src/costwise/feedback/detector.py` | Retry detection via content fingerprint matching (Jaccard similarity ≥ 0.7) |
| `src/costwise/feedback/tuner.py` | Auto-tunes simple/complex thresholds based on retry events (±0.01 nudges) |
| `src/costwise/feedback/fingerprint.py` | SHA-256 fingerprinting of normalized message content |
| `src/costwise/proxy/server.py` | FastAPI proxy: intercepts requests, applies full routing pipeline, forwards to provider |
| `src/costwise/config/schema.py` | Configuration dataclasses (RoutingConfig, BudgetConfig, FeedbackConfig, etc.) |

### Current Signal Weights (classifier.py:ClassifierConfig)

```python
w_tools = 0.12              # tool presence
w_token_count = 0.10        # token count (normalized 500–10K)
w_code = 0.12               # code block presence
w_depth = 0.08              # conversation depth (normalized 2–20 messages)
w_error = 0.18              # error keywords (BINARY: 1.0 or 0.0)
w_retry = 0.18              # retry keywords (BINARY: 1.0 or 0.0)
w_images = 0.07             # image/vision content
w_code_tools_compound = 0.15 # code + tools together
w_graph_complexity = 0.15   # code graph centrality (0.0–1.0)
```

### Current Pricing (per MTok, USD)

| Model | Tier | Input | Output | Provider |
|-------|------|-------|--------|----------|
| claude-opus-4-7 | COMPLEX | $5.00 | $25.00 | Anthropic |
| claude-sonnet-4-6 | MEDIUM | $3.00 | $15.00 | Anthropic |
| claude-haiku-4-5 | SIMPLE | $1.00 | $5.00 | Anthropic |
| gpt-5 | COMPLEX | $1.25 | $10.00 | OpenAI |
| gpt-4.1 | MEDIUM | $2.00 | $8.00 | OpenAI |
| gpt-4.1-mini | SIMPLE | $0.40 | $1.60 | OpenAI |
| gemini-2.5-pro | COMPLEX | $1.25 | $10.00 | Google |
| gemini-2.5-flash | MEDIUM | $0.30 | $2.50 | Google |
| gemini-2.5-flash-lite | SIMPLE | $0.10 | $0.40 | Google |

### Current Feedback Loop

1. **Retry detection** (`detector.py`): Fingerprints messages, looks for similar requests within 5 minutes. If found and the original was downgraded → `RetryEvent`.
2. **Retry override** (`server.py:228–240`): If retry detected on a downgraded request, upgrade tier for this attempt.
3. **Threshold tuning** (`tuner.py`): On retry events, nudge `simple_threshold` or `complex_threshold` down by 0.01 (bounded, rate-limited to 5/hour). Relaxes thresholds when false-downgrade rate is very low.

---

## Phase 1: Expected Cost Optimization

### Goal
Replace "pick cheapest model in tier" with "pick model that minimizes expected total cost including retry risk."

### Economic Insight
```
expected_cost(model) = base_cost(model) + P(retry|tier) × (base_cost(model) + cheapest_cost(tier+1))
```

A model at $0.10/MTok with 20% retry probability costs MORE than $0.40/MTok with 2% retry probability:
- $0.10 model: 0.10 + 0.20 × (0.10 + 0.40) = 0.10 + 0.10 = **$0.20/MTok effective**
- $0.40 model: 0.40 + 0.02 × (0.40 + 3.00) = 0.40 + 0.07 = **$0.47/MTok effective**

Wait — in this case the $0.10 model still wins. But consider SIMPLE→MEDIUM upgrade costs:
- Haiku ($1/$5) with 15% retry → MEDIUM cheapest is Flash ($0.30/$2.50):
  - base = (1000×1 + 300×5)/1M = 0.0025
  - retry_penalty = 0.15 × (0.0025 + (1000×0.30 + 300×2.50)/1M) = 0.15 × (0.0025 + 0.00105) = 0.00053
  - expected = **$0.00303**
- Flash ($0.30/$2.50) with 2% retry → same tier, no upgrade:
  - base = (1000×0.30 + 300×2.50)/1M = 0.00105
  - retry_penalty = 0.02 × 0.00105 = 0.00002
  - expected = **$0.00107**

Flash is cheaper AND more reliable. The router should pick Flash over Haiku in this scenario even though Haiku is in the "correct" tier.

### Files to Create

#### `src/costwise/core/expected_cost.py` (NEW)

```python
"""Expected cost calculator: accounts for retry risk in model selection."""

from __future__ import annotations

from costwise.core.models import ModelInfo, Tier
from costwise.core.pricing import PricingRegistry


# Map each tier to the tier a retry would escalate to
_UPGRADE_TIER = {Tier.SIMPLE: Tier.MEDIUM, Tier.MEDIUM: Tier.COMPLEX}


def estimate_retry_probability(
    tier: Tier,
    confidence: float,
    per_tier_rates: dict[str, float],
) -> float:
    """Estimate P(retry) for a routing decision.

    Args:
        tier: The classified tier.
        confidence: Classifier confidence (0.0–1.0). Lower confidence → higher retry risk.
        per_tier_rates: Historical retry rates per tier from the tracking store.
            Keys are tier names ("SIMPLE", "MEDIUM", "COMPLEX"), values are rates (0.0–1.0).

    Returns:
        Estimated retry probability (0.0–1.0).
    """
    base_rate = per_tier_rates.get(tier.value, 0.03)  # default 3%

    # Low confidence inflates retry probability.
    # At confidence=1.0, multiplier=1.0; at confidence=0.0, multiplier=3.0
    confidence_multiplier = 1.0 + 2.0 * (1.0 - confidence)

    return min(1.0, base_rate * confidence_multiplier)


def expected_total_cost(
    model: ModelInfo,
    input_tokens: int,
    output_tokens: int,
    retry_prob: float,
    registry: PricingRegistry,
) -> float:
    """Compute expected total cost including retry penalty.

    expected_cost = base_cost + P(retry) × (base_cost + cheapest_upgrade_cost)

    The retry penalty assumes:
    1. The failed attempt's full cost is wasted
    2. The retry goes to the cheapest model in the next tier up
    3. If already at COMPLEX tier, retry goes to the same tier (no upgrade)
    """
    base_cost = (
        input_tokens * model.input_cost_per_mtok / 1_000_000
        + output_tokens * model.output_cost_per_mtok / 1_000_000
    )

    if retry_prob <= 0.0:
        return base_cost

    # Find cheapest model in the upgrade tier
    upgrade_tier = _UPGRADE_TIER.get(model.tier, model.tier)
    upgrade_candidates = registry.models_for_tier(upgrade_tier)

    if not upgrade_candidates:
        # No upgrade tier available; retry cost = just repeating with same model
        return base_cost + retry_prob * base_cost

    cheapest_upgrade = min(
        upgrade_candidates,
        key=lambda m: (
            input_tokens * m.input_cost_per_mtok / 1_000_000
            + output_tokens * m.output_cost_per_mtok / 1_000_000
        ),
    )
    upgrade_cost = (
        input_tokens * cheapest_upgrade.input_cost_per_mtok / 1_000_000
        + output_tokens * cheapest_upgrade.output_cost_per_mtok / 1_000_000
    )

    return base_cost + retry_prob * (base_cost + upgrade_cost)
```

### Files to Modify

#### `src/costwise/tracking/store.py`

Add this method to `TrackingStore`:

```python
async def get_retry_rate_by_tier(self, window_minutes: int = 1440) -> dict[str, float]:
    """Per-tier retry rate over the given window (default 24h).

    Returns dict like {"SIMPLE": 0.05, "MEDIUM": 0.02, "COMPLEX": 0.01}.
    """
    def _query() -> dict[str, float]:
        conn = self._get_conn()
        # Count retries per original tier
        retry_rows = conn.execute(
            """SELECT original_tier, COUNT(*) as retry_count
               FROM retry_events
               WHERE was_downgraded = 1
                 AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                     datetime('now', ? || ' minutes'))
               GROUP BY original_tier""",
            (str(-window_minutes),),
        ).fetchall()
        # Count total requests per tier
        total_rows = conn.execute(
            """SELECT tier, COUNT(*) as total_count
               FROM routing_decisions
               WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                     datetime('now', ? || ' minutes'))
               GROUP BY tier""",
            (str(-window_minutes),),
        ).fetchall()
        totals = {row["tier"]: row["total_count"] for row in total_rows}
        rates = {}
        for row in retry_rows:
            tier = row["original_tier"]
            total = totals.get(tier, 0)
            rates[tier] = row["retry_count"] / total if total > 0 else 0.0
        # Fill in tiers with no retries
        for tier in ("SIMPLE", "MEDIUM", "COMPLEX"):
            if tier not in rates:
                rates[tier] = 0.0
        return rates

    return await asyncio.to_thread(_query)
```

#### `src/costwise/core/arbitrage.py`

Modify `select_cheapest` to accept an optional retry probability and use expected cost:

```python
def select_cheapest(
    registry: PricingRegistry,
    tier: Tier,
    *,
    estimated_input_tokens: int = 1000,
    estimated_output_tokens: int = 500,
    enabled_providers: set[str] | None = None,
    excluded_providers: set[str] | None = None,
    needs_tools: bool = False,
    needs_vision: bool = False,
    health_tracker: ProviderHealthTracker | None = None,
    retry_probability: float | None = None,   # NEW PARAMETER
) -> ArbitrageResult | None:
```

Change the `_cost` lambda and ranking:

```python
    def _cost(m: ModelInfo) -> float:
        return (
            estimated_input_tokens * m.input_cost_per_mtok / 1_000_000
            + estimated_output_tokens * m.output_cost_per_mtok / 1_000_000
        )

    # NEW: if retry_probability is provided, rank by expected cost
    if retry_probability is not None and retry_probability > 0:
        from costwise.core.expected_cost import expected_total_cost
        ranked = sorted(
            candidates,
            key=lambda m: expected_total_cost(
                m, estimated_input_tokens, estimated_output_tokens,
                retry_probability, registry,
            ),
        )
    else:
        ranked = sorted(candidates, key=_cost)
```

#### `src/costwise/core/router.py`

In the `Router` class, add a store reference and pass retry probability to `select_cheapest`:

1. Add `store: TrackingStore | None = None` to `Router.__init__` parameters.
2. In `_reroute`, before calling `select_cheapest`:

```python
    # Fetch per-tier retry rates (cached, async)
    retry_prob = None
    if self._store:
        import asyncio
        try:
            per_tier_rates = asyncio.get_event_loop().run_until_complete(
                self._store.get_retry_rate_by_tier()
            )
        except RuntimeError:
            per_tier_rates = {}
        from costwise.core.expected_cost import estimate_retry_probability
        retry_prob = estimate_retry_probability(
            tier, classification.confidence, per_tier_rates,
        )
```

Then pass `retry_probability=retry_prob` to `select_cheapest`.

**Note**: The Router currently doesn't have access to the TrackingStore. It needs to be passed in from `server.py` where the store is available. Modify `_build_router` in `server.py` to pass the store:

```python
def _build_router(config, health_tracker, budget_enforcer, store=None):
    ...
    return Router(
        registry=PricingRegistry(),
        config=router_cfg,
        health_tracker=health_tracker,
        budget_enforcer=budget_enforcer,
        store=store,
    )
```

**Important**: Since `_reroute` is called from the sync `route()` method but the store queries are async, you'll need to either:
- (a) Add a sync version of `get_retry_rate_by_tier` that bypasses `asyncio.to_thread`, OR
- (b) Cache the per-tier rates periodically (recommended — call it every 100 requests from the server's async context and pass the cached dict to `route()`), OR
- (c) Add `retry_rates` as an optional parameter to `route()` and compute it in the async server handler.

**Recommended approach: (c)** — compute `per_tier_rates` in `server.py`'s async `proxy_request()` handler and pass it through `route()`. This keeps the router sync and avoids async-in-sync complexity.

### Integration Points in `server.py`

In `proxy_request()`, before calling `router.route()`:

```python
    # Fetch retry rates for expected cost optimization
    per_tier_rates = await store.get_retry_rate_by_tier(window_minutes=1440)
    decision = router.route(request_body, graph=graph, retry_rates=per_tier_rates)
```

This requires adding `retry_rates: dict[str, float] | None = None` to `Router.route()` and passing it through to `_reroute`.

### Tests

Add to existing test file or create `tests/test_expected_cost.py`:

1. **Test expected cost ranking inversion**: Given two models where the cheaper one has high retry probability, verify `expected_total_cost` makes the pricier model win.
2. **Test retry probability estimation**: Verify that low confidence inflates the base retry rate.
3. **Test integration**: End-to-end test that routing selects different models when retry probability is provided vs. not.
4. **Test backward compatibility**: When `retry_probability=None` (default), verify `select_cheapest` behavior is unchanged.

---

## Phase 2: Semantic Signal Enrichment

### Goal
Add intent detection, graduated error severity, and multi-file scope detection to the signal extraction pipeline.

### Files to Modify

#### `src/costwise/core/models.py` — Add new fields to SignalBundle

Add these fields to the `SignalBundle` class:

```python
class SignalBundle(BaseModel):
    # ... existing fields ...

    # NEW semantic signals (Phase 2)
    intent: str = "unknown"           # generate|refactor|explain|fix|debug|test|review|chat|unknown
    error_severity: float = 0.0       # 0.0=none, 0.3=warning, 0.6=runtime error, 1.0=critical
    multi_file_scope: bool = False    # references multiple distinct file paths
    referenced_file_count: int = 0    # number of distinct file paths referenced
```

#### `src/costwise/core/signals.py` — Add intent, error severity, file scope extraction

Add these regex patterns and functions:

```python
# Intent detection patterns (applied to LAST user message only)
_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("explain", re.compile(
        r"\b(explain|what does|how does|what is|describe|walk me through|tell me about"
        r"|why does|what\'s the purpose|understand)\b", re.IGNORECASE)),
    ("refactor", re.compile(
        r"\b(refactor|restructure|reorganize|clean up|simplify|extract|decompose"
        r"|split into|move .+ to|rename)\b", re.IGNORECASE)),
    ("generate", re.compile(
        r"\b(write|create|implement|add|generate|build|make|set up|scaffold"
        r"|new file|new function|new class|new component)\b", re.IGNORECASE)),
    ("fix", re.compile(
        r"\b(fix|resolve|repair|patch|correct|address|handle .+ error"
        r"|solve|work around)\b", re.IGNORECASE)),
    ("debug", re.compile(
        r"\b(debug|investigate|diagnose|figure out why|trace|root cause"
        r"|why is .+ (failing|broken|not working)|step through)\b", re.IGNORECASE)),
    ("test", re.compile(
        r"\b(test|write tests|add tests|unit test|integration test|test case"
        r"|coverage|spec|assert)\b", re.IGNORECASE)),
    ("review", re.compile(
        r"\b(review|check|audit|look over|feedback|comments on"
        r"|what do you think|is this (correct|right|ok|good))\b", re.IGNORECASE)),
    ("chat", re.compile(
        r"\b(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure|got it)\b", re.IGNORECASE)),
]

# Error severity patterns (graduated, not binary)
_ERROR_SEVERITY_CRITICAL = re.compile(
    r"\b(crash|segfault|SIGSEGV|panic|OOM|out of memory|kernel panic"
    r"|fatal|CRITICAL|production .+(down|error|failure)|data loss"
    r"|corrupted|unrecoverable)\b", re.IGNORECASE)

_ERROR_SEVERITY_RUNTIME = re.compile(
    r"\b(TypeError|ValueError|KeyError|AttributeError|ImportError"
    r"|NullPointerException|IndexOutOfBoundsException"
    r"|RuntimeError|exception|traceback|stack trace"
    r"|undefined is not|cannot read propert"
    r"|compilation failed|build failed|test failed)\b", re.IGNORECASE)

_ERROR_SEVERITY_WARNING = re.compile(
    r"\b(warning|deprecated|deprecation|lint|linting"
    r"|unused|unreachable|shadowed|type mismatch)\b", re.IGNORECASE)

# File path detection
_FILE_PATH_RE = re.compile(
    r"(?:^|\s|[\"'`])("
    r"(?:[a-zA-Z]:)?(?:[/\\][\w\-.]+)+(?:\.\w+)"  # absolute or relative paths with extension
    r"|[\w\-./]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|cpp|c|h|cs|swift|kt)"  # file.ext patterns
    r")")
```

Add these helper functions:

```python
def _detect_intent(messages: list[dict]) -> str:
    """Detect task intent from the last user message."""
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
            elif isinstance(content, list):
                last_user_msg = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            break

    if not last_user_msg:
        return "unknown"

    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(last_user_msg):
            return intent

    return "unknown"


def _compute_error_severity(text: str) -> float:
    """Compute graduated error severity (0.0–1.0)."""
    if _ERROR_SEVERITY_CRITICAL.search(text):
        return 1.0
    if _ERROR_SEVERITY_RUNTIME.search(text):
        return 0.6
    if _ERROR_SEVERITY_WARNING.search(text):
        return 0.3
    return 0.0


def _detect_file_scope(text: str) -> tuple[bool, int]:
    """Detect multi-file scope from file path references in text."""
    matches = set(_FILE_PATH_RE.findall(text))
    count = len(matches)
    return count > 1, count
```

Modify `extract_signals()` to populate the new fields:

```python
def extract_signals(request_body: dict) -> SignalBundle:
    messages = request_body.get("messages", [])
    # ... existing signal extraction ...
    full_text = _extract_text(messages)

    # NEW: semantic signals
    intent = _detect_intent(messages)
    error_severity = _compute_error_severity(full_text)
    multi_file_scope, referenced_file_count = _detect_file_scope(full_text)

    return SignalBundle(
        # ... existing fields ...
        has_error_context=bool(_ERROR_KEYWORDS.search(full_text)),  # keep for backward compat
        # NEW fields
        intent=intent,
        error_severity=error_severity,
        multi_file_scope=multi_file_scope,
        referenced_file_count=referenced_file_count,
    )
```

#### `src/costwise/core/classifier.py` — Add new weights and scoring

Add to `ClassifierConfig`:

```python
@dataclass
class ClassifierConfig:
    # ... existing weights ...

    # NEW Phase 2 weights
    w_intent: float = 0.10
    w_multi_file: float = 0.06
    # w_error stays at 0.18 but now uses graduated severity instead of binary
```

Rebalance existing weights so they still sum to ~1.0. Suggested rebalance:
```python
    w_tools: float = 0.10          # was 0.12
    w_token_count: float = 0.08    # was 0.10
    w_code: float = 0.10           # was 0.12
    w_depth: float = 0.06          # was 0.08
    w_error: float = 0.16          # was 0.18, now uses graduated severity
    w_retry: float = 0.16          # was 0.18
    w_images: float = 0.05         # was 0.07
    w_code_tools_compound: float = 0.13  # was 0.15
    w_graph_complexity: float = 0.10     # was 0.15
    # NEW
    w_intent: float = 0.10
    w_multi_file: float = 0.06
```

In `classify()`, add the new signal scoring:

```python
    # Intent complexity bias
    _INTENT_COMPLEXITY = {
        "chat": 0.0,
        "explain": 0.1,
        "review": 0.2,
        "test": 0.3,
        "fix": 0.5,
        "debug": 0.55,
        "generate": 0.6,
        "refactor": 0.7,
        "unknown": 0.35,  # neutral — doesn't bias
    }
    intent_score = _INTENT_COMPLEXITY.get(signals.intent, 0.35)
    breakdown["intent"] = intent_score * cfg.w_intent

    # Graduated error severity (replaces binary error signal)
    error_score = signals.error_severity  # 0.0, 0.3, 0.6, or 1.0
    breakdown["error"] = error_score * cfg.w_error

    # Multi-file scope bonus
    multi_file_score = 0.0
    if signals.multi_file_scope and signals.has_code:
        multi_file_score = min(1.0, 0.5 + signals.referenced_file_count * 0.1)
    breakdown["multi_file"] = multi_file_score * cfg.w_multi_file
```

### Backward Compatibility
- `has_error_context` stays in SignalBundle (existing code may reference it)
- New fields default to values that produce zero scoring contribution (`intent="unknown"` → 0.35 × w_intent = 0.035, which roughly matches the removed weight from rebalancing)
- Old configs without the new weights will use defaults

### Tests

Add `tests/test_semantic_signals.py`:

1. **Intent detection accuracy**: Test 5+ prompts per intent category.
   ```python
   assert _detect_intent([{"role": "user", "content": "explain how the auth middleware works"}]) == "explain"
   assert _detect_intent([{"role": "user", "content": "refactor the database layer into separate modules"}]) == "refactor"
   assert _detect_intent([{"role": "user", "content": "write a function that validates emails"}]) == "generate"
   ```

2. **Error severity gradation**: Verify severity levels.
   ```python
   assert _compute_error_severity("there's a deprecation warning here") == 0.3
   assert _compute_error_severity("getting TypeError: cannot read property") == 0.6
   assert _compute_error_severity("the server crashed with OOM") == 1.0
   assert _compute_error_severity("the code looks fine") == 0.0
   ```

3. **Multi-file scope**: Verify file path detection.
   ```python
   assert _detect_file_scope("update src/auth.py and src/middleware.py") == (True, 2)
   assert _detect_file_scope("fix the bug in main.py") == (False, 1)
   ```

4. **Classification regression**: Run the existing test suite to verify existing classifications aren't broken.

---

## Phase 3: Borderline Case Handling (Soft Boundaries)

### Goal
Requests near tier boundaries (score within ±0.05 of a threshold) should use cost-benefit analysis instead of hard cutoffs.

### Prerequisite
Phase 1 (Expected Cost Optimization) — this phase uses `expected_total_cost` to compare tiers.

### Files to Modify

#### `src/costwise/core/classifier.py`

Add to `ClassifierConfig`:
```python
    boundary_zone: float = 0.05  # score within this distance of a threshold = "borderline"
```

Add to `ClassificationResult`:
```python
    is_borderline: bool = False
    borderline_alternative_tier: Tier | None = None
```

In `classify()`, after computing score and tier, detect borderline cases:

```python
    # Detect borderline classifications
    is_borderline = False
    borderline_alt = None

    if tier == Tier.SIMPLE:
        distance_to_medium = cfg.simple_threshold - score
        if distance_to_medium < cfg.boundary_zone:
            is_borderline = True
            borderline_alt = Tier.MEDIUM
    elif tier == Tier.MEDIUM:
        distance_to_simple = score - cfg.simple_threshold
        distance_to_complex = cfg.complex_threshold - score
        if distance_to_simple < cfg.boundary_zone:
            is_borderline = True
            borderline_alt = Tier.SIMPLE
        elif distance_to_complex < cfg.boundary_zone:
            is_borderline = True
            borderline_alt = Tier.COMPLEX
    elif tier == Tier.COMPLEX:
        distance_to_medium = score - cfg.complex_threshold
        if distance_to_medium < cfg.boundary_zone:
            is_borderline = True
            borderline_alt = Tier.MEDIUM

    return ClassificationResult(
        tier=tier,
        score=score,
        confidence=max(0.0, confidence),
        breakdown=breakdown,
        is_borderline=is_borderline,
        borderline_alternative_tier=borderline_alt,
    )
```

#### `src/costwise/core/router.py`

In `_reroute`, when the classification is borderline, compare expected costs for both tiers:

```python
    # Borderline case: compare expected cost for both tiers
    if (
        classification.is_borderline
        and classification.borderline_alternative_tier
        and retry_prob is not None
    ):
        from costwise.core.expected_cost import expected_total_cost

        # Get cheapest model for each tier
        primary_result = select_cheapest(
            self._registry, tier,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=estimated_output,
            retry_probability=retry_prob,
            # ... other filters ...
        )
        alt_result = select_cheapest(
            self._registry, classification.borderline_alternative_tier,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=estimated_output,
            retry_probability=retry_prob,
            # ... other filters ...
        )

        if primary_result and alt_result:
            primary_expected = expected_total_cost(
                primary_result.chosen, input_tokens, estimated_output,
                retry_prob, self._registry,
            )
            alt_expected = expected_total_cost(
                alt_result.chosen, input_tokens, estimated_output,
                retry_prob, self._registry,
            )
            if alt_expected < primary_expected:
                tier = classification.borderline_alternative_tier
                result = alt_result
                # Update classification reason
```

### Tests

1. **Borderline detection**: Verify that scores within `boundary_zone` of thresholds are flagged.
2. **Cost comparison**: Given a borderline SIMPLE/MEDIUM case with high SIMPLE retry rate, verify MEDIUM is chosen.
3. **Non-borderline pass-through**: Verify that scores well within a tier are not flagged as borderline.

---

## Phase 4: Signal Snapshot Tracking + Adaptive Weights

### Goal
Store signal snapshots alongside routing outcomes. Use the accumulated data to adapt signal weights based on which signals actually predict retries.

### Prerequisite
Phase 2 (Semantic Signals) — so we're tracking the enriched signal set, not just the original 12 signals.

### Files to Modify

#### `src/costwise/tracking/schema.sql`

Add new table:

```sql
CREATE TABLE IF NOT EXISTS signal_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        INTEGER NOT NULL REFERENCES routing_decisions(id),
    token_count       INTEGER,
    has_tools         INTEGER,
    tool_count        INTEGER,
    has_code          INTEGER,
    code_block_count  INTEGER,
    conversation_depth INTEGER,
    has_error_context INTEGER,
    error_severity    REAL,
    has_retry_context INTEGER,
    image_count       INTEGER,
    intent            TEXT,
    multi_file_scope  INTEGER,
    referenced_file_count INTEGER,
    graph_complexity  REAL,
    ponytail_mode     TEXT
);

CREATE INDEX IF NOT EXISTS idx_signal_request ON signal_snapshots(request_id);
```

#### `src/costwise/tracking/store.py`

Add methods:

```python
async def record_signal_snapshot(self, request_id: int, signals: SignalBundle) -> None:
    """Store signal values alongside a routing decision for later analysis."""
    def _insert() -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO signal_snapshots
               (request_id, token_count, has_tools, tool_count, has_code,
                code_block_count, conversation_depth, has_error_context,
                error_severity, has_retry_context, image_count, intent,
                multi_file_scope, referenced_file_count, graph_complexity,
                ponytail_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id, signals.token_count, int(signals.has_tools),
                signals.tool_count, int(signals.has_code), signals.code_block_count,
                signals.conversation_depth, int(signals.has_error_context),
                signals.error_severity, int(signals.has_retry_context),
                signals.image_count, signals.intent,
                int(signals.multi_file_scope), signals.referenced_file_count,
                signals.graph_complexity, signals.ponytail_mode,
            ),
        )
        conn.commit()
    await asyncio.to_thread(_insert)


async def get_signal_retry_correlations(self, window_hours: int = 24) -> dict[str, float]:
    """For each signal, compute mean(signal | retry) - mean(signal | no retry).

    Positive correlation = signal predicts retries (should increase weight).
    Negative correlation = signal anti-predicts retries (should decrease weight).
    """
    def _query() -> dict[str, float]:
        conn = self._get_conn()
        signal_cols = [
            "token_count", "has_tools", "tool_count", "has_code",
            "code_block_count", "conversation_depth", "has_error_context",
            "error_severity", "has_retry_context", "image_count",
            "multi_file_scope", "referenced_file_count", "graph_complexity",
        ]
        correlations = {}
        for col in signal_cols:
            row = conn.execute(f"""
                SELECT
                    AVG(CASE WHEN re.id IS NOT NULL THEN s.{col} END) as mean_retry,
                    AVG(CASE WHEN re.id IS NULL THEN s.{col} END) as mean_no_retry
                FROM signal_snapshots s
                JOIN routing_decisions rd ON s.request_id = rd.id
                LEFT JOIN retry_events re ON rd.id = re.original_request_id
                WHERE rd.timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                    datetime('now', ? || ' hours'))
            """, (str(-window_hours),)).fetchone()
            mean_retry = row["mean_retry"] or 0.0
            mean_no_retry = row["mean_no_retry"] or 0.0
            correlations[col] = mean_retry - mean_no_retry
        return correlations
    return await asyncio.to_thread(_query)
```

### Files to Create

#### `src/costwise/feedback/weight_learner.py` (NEW)

```python
"""Adaptive weight learner: adjusts classifier signal weights based on retry correlations."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from costwise.core.classifier import ClassifierConfig
from costwise.tracking.store import TrackingStore

logger = logging.getLogger(__name__)

# Signal name → ClassifierConfig weight attribute mapping
_SIGNAL_TO_WEIGHT = {
    "has_tools": "w_tools",
    "token_count": "w_token_count",
    "has_code": "w_code",
    "conversation_depth": "w_depth",
    "error_severity": "w_error",
    "has_retry_context": "w_retry",
    "image_count": "w_images",
    "multi_file_scope": "w_multi_file",
    "graph_complexity": "w_graph_complexity",
}

# Maximum drift from default (±30%)
_MAX_DRIFT = 0.30


@dataclass
class WeightLearner:
    store: TrackingStore
    classifier_config: ClassifierConfig
    _default_weights: dict[str, float] = field(default_factory=dict, init=False)
    _last_adjustment: float = field(default=0.0, init=False)
    _min_adjustment_interval: float = 3600.0  # 1 hour between adjustments

    def __post_init__(self) -> None:
        # Snapshot the initial weights as defaults
        for signal, attr in _SIGNAL_TO_WEIGHT.items():
            self._default_weights[attr] = getattr(self.classifier_config, attr)

    async def maybe_adjust(self, min_requests: int = 100) -> bool:
        """Check correlations and adjust weights if enough data and enough time has passed."""
        if time.monotonic() - self._last_adjustment < self._min_adjustment_interval:
            return False

        correlations = await self.store.get_signal_retry_correlations(window_hours=24)

        if not correlations:
            return False

        # Normalize correlations to a relative scale
        max_corr = max(abs(v) for v in correlations.values()) or 1.0

        adjusted = False
        for signal, attr in _SIGNAL_TO_WEIGHT.items():
            if signal not in correlations:
                continue

            default_weight = self._default_weights[attr]
            corr = correlations[signal] / max_corr  # -1.0 to 1.0

            # Positive correlation → increase weight (signal predicts retries, should upweight)
            # Negative correlation → decrease weight
            adjustment = corr * _MAX_DRIFT * default_weight
            new_weight = default_weight + adjustment

            # Bound: never drift more than ±30% from default
            lower_bound = default_weight * (1.0 - _MAX_DRIFT)
            upper_bound = default_weight * (1.0 + _MAX_DRIFT)
            new_weight = max(lower_bound, min(upper_bound, new_weight))

            old_weight = getattr(self.classifier_config, attr)
            if abs(new_weight - old_weight) > 0.001:
                setattr(self.classifier_config, attr, round(new_weight, 4))
                logger.info("WeightLearner: %s %.4f → %.4f (corr=%.3f)", attr, old_weight, new_weight, corr)
                adjusted = True

        if adjusted:
            self._last_adjustment = time.monotonic()

        return adjusted
```

#### `src/costwise/proxy/server.py`

In `proxy_request()`, after `request_id = await store.record_request(record)`:

```python
    # Record signal snapshot for adaptive weight learning
    signals = router.last_signals  # need to surface this from router
    if signals:
        await store.record_signal_snapshot(request_id, signals)
```

The Router needs to expose the signals it extracted. Add to `Router`:

```python
    @property
    def last_signals(self) -> SignalBundle | None:
        return self._last_signals
```

And set `self._last_signals = signals` in `route()` after `extract_signals()`.

Also in `create_app`, create the weight learner and periodically trigger it:

```python
    weight_learner = WeightLearner(store, router.config.classifier) if config.feedback.enabled else None

    # In proxy_request, after recording the request:
    if weight_learner and request_count % 100 == 0:
        await weight_learner.maybe_adjust()
```

### Tests

1. **Weight bounding**: Verify weights never drift beyond ±30% of defaults.
2. **Correlation-to-weight mapping**: Given mock correlations, verify correct weight adjustments.
3. **Rate limiting**: Verify `maybe_adjust` respects the 1-hour interval.
4. **End-to-end**: Simulate requests + retries, verify weight learner shifts weights in the expected direction.

---

## Testing Strategy (All Phases)

### Existing Test Suite
Run `pytest` after each phase to ensure no regressions. The project has ~286 tests.

### New Test Files Per Phase

| Phase | Test File | Key Tests |
|-------|-----------|-----------|
| 1 | `tests/test_expected_cost.py` | Expected cost ranking inversion, retry probability estimation, backward compatibility |
| 2 | `tests/test_semantic_signals.py` | Intent detection accuracy, error severity gradation, file scope detection, classification regression |
| 3 | `tests/test_borderline.py` | Borderline detection, cost comparison, non-borderline pass-through |
| 4 | `tests/test_weight_learner.py` | Weight bounding, correlation mapping, rate limiting |

### Integration Verification

After each phase, run:
```bash
# Run full test suite
pytest

# Start the proxy and send test requests
costwise doctor  # verify all components healthy
python -c "from costwise.core.router import Router; r = Router(); print(r.route({'model': 'claude-opus-4-7', 'messages': [{'role': 'user', 'content': 'explain this function'}]}))"
```

---

## Phase Dependencies

```
Phase 1 (Expected Cost)     ← independent, ship first
Phase 2 (Semantic Signals)  ← independent, ship first or second
Phase 3 (Soft Boundaries)   ← depends on Phase 1
Phase 4 (Adaptive Weights)  ← depends on Phase 2 (richer signals to track)
```

Phases 1 and 2 can be done in parallel. Phase 3 requires Phase 1. Phase 4 requires Phase 2 and benefits from Phase 1.
