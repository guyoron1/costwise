"""Rule-based complexity classifier: SignalBundle → Tier.

Produces a complexity score from 0.0 (trivial) to 1.0 (maximum complexity),
then maps to SIMPLE / MEDIUM / COMPLEX via configurable thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from costwise.core.models import SignalBundle, Tier


@dataclass
class ClassifierConfig:
    """Tunable weights and thresholds for the classifier."""

    simple_threshold: float = 0.20
    complex_threshold: float = 0.55

    # Signal weights (should sum to roughly 1.0 for interpretability)
    w_tools: float = 0.09
    w_token_count: float = 0.07
    w_code: float = 0.09
    w_depth: float = 0.05
    w_error: float = 0.14
    w_retry: float = 0.14
    w_images: float = 0.05
    w_code_tools_compound: float = 0.12
    w_graph_complexity: float = 0.09

    # Semantic signal weights (Phase 2)
    w_intent: float = 0.10
    w_multi_file: float = 0.06

    # Borderline zone (Phase 3): scores within this distance of a threshold
    # trigger cost-benefit comparison between adjacent tiers
    boundary_zone: float = 0.05

    # Token count breakpoints for normalization
    token_low: int = 500
    token_high: int = 10_000

    # Conversation depth breakpoints
    depth_low: int = 2
    depth_high: int = 20

    # Ponytail modifiers (negative = bias toward SIMPLE)
    ponytail_ultra_bias: float = -0.15
    ponytail_full_bias: float = -0.08
    ponytail_lite_bias: float = -0.03


@dataclass
class ClassificationResult:
    tier: Tier
    score: float
    confidence: float
    breakdown: dict[str, float] = field(default_factory=dict)
    is_borderline: bool = False
    borderline_alternative_tier: Tier | None = None

    @property
    def reason(self) -> str:
        top_signals = sorted(self.breakdown.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
        parts = [f"{k}={v:+.2f}" for k, v in top_signals if abs(v) > 0.01]
        return f"{self.tier.value} (score={self.score:.2f}): {', '.join(parts)}"


def classify(signals: SignalBundle, config: ClassifierConfig | None = None) -> ClassificationResult:
    cfg = config or ClassifierConfig()
    breakdown: dict[str, float] = {}

    # Tool usage: tools present → more likely to be MEDIUM+
    tool_score = 0.0
    if signals.has_tools:
        tool_score = min(1.0, 0.3 + signals.tool_count * 0.1)
    breakdown["tools"] = tool_score * cfg.w_tools

    # Token count: more tokens → more complex context
    if signals.token_count <= cfg.token_low:
        token_score = 0.0
    elif signals.token_count >= cfg.token_high:
        token_score = 1.0
    else:
        token_score = (signals.token_count - cfg.token_low) / (cfg.token_high - cfg.token_low)
    breakdown["tokens"] = token_score * cfg.w_token_count

    # Code presence: code blocks suggest editing/generation tasks
    code_score = 0.0
    if signals.has_code:
        code_score = min(1.0, 0.4 + signals.code_block_count * 0.15)
    breakdown["code"] = code_score * cfg.w_code

    # Conversation depth: deeper conversations → ongoing complex work
    if signals.conversation_depth <= cfg.depth_low:
        depth_score = 0.0
    elif signals.conversation_depth >= cfg.depth_high:
        depth_score = 1.0
    else:
        depth_score = (signals.conversation_depth - cfg.depth_low) / (
            cfg.depth_high - cfg.depth_low
        )
    breakdown["depth"] = depth_score * cfg.w_depth

    # Error severity: graduated instead of binary (Phase 2)
    error_score = signals.error_severity if signals.error_severity > 0.0 else (
        1.0 if signals.has_error_context else 0.0
    )
    breakdown["error"] = error_score * cfg.w_error

    # Retry context: retries mean the previous (possibly cheaper) model failed
    retry_score = 1.0 if signals.has_retry_context else 0.0
    breakdown["retry"] = retry_score * cfg.w_retry

    # Images: vision tasks need capable models
    image_score = min(1.0, signals.image_count * 0.5) if signals.image_count > 0 else 0.0
    breakdown["images"] = image_score * cfg.w_images

    # Compound: tools + code together = code editing work → at least MEDIUM
    compound_score = 0.0
    if signals.has_tools and signals.has_code:
        compound_score = min(1.0, 0.6 + signals.code_block_count * 0.1 + signals.tool_count * 0.05)
    breakdown["code+tools"] = compound_score * cfg.w_code_tools_compound

    # Graph complexity: high centrality files need more capable models
    breakdown["graph"] = signals.graph_complexity * cfg.w_graph_complexity

    # Intent complexity bias (Phase 2)
    _INTENT_COMPLEXITY = {
        "chat": 0.0,
        "explain": 0.1,
        "review": 0.2,
        "test": 0.3,
        "fix": 0.5,
        "debug": 0.55,
        "generate": 0.6,
        "refactor": 0.7,
        "unknown": 0.35,
    }
    intent_score = _INTENT_COMPLEXITY.get(signals.intent, 0.35)
    breakdown["intent"] = intent_score * cfg.w_intent

    # Multi-file scope bonus (Phase 2)
    multi_file_score = 0.0
    if signals.multi_file_scope and signals.has_code:
        multi_file_score = min(1.0, 0.5 + signals.referenced_file_count * 0.1)
    breakdown["multi_file"] = multi_file_score * cfg.w_multi_file

    # Sum weighted signals
    raw_score = sum(breakdown.values())

    # Apply Ponytail bias
    ponytail_bias = 0.0
    if signals.ponytail_mode == "ultra":
        ponytail_bias = cfg.ponytail_ultra_bias
    elif signals.ponytail_mode == "full":
        ponytail_bias = cfg.ponytail_full_bias
    elif signals.ponytail_mode == "lite":
        ponytail_bias = cfg.ponytail_lite_bias
    breakdown["ponytail"] = ponytail_bias

    score = max(0.0, min(1.0, raw_score + ponytail_bias))

    # Map score to tier
    if score < cfg.simple_threshold:
        tier = Tier.SIMPLE
    elif score < cfg.complex_threshold:
        tier = Tier.MEDIUM
    else:
        tier = Tier.COMPLEX

    # Confidence: how far from the nearest threshold boundary
    if tier == Tier.SIMPLE:
        distance = cfg.simple_threshold - score
        confidence = min(1.0, distance / cfg.simple_threshold) if cfg.simple_threshold > 0 else 1.0
    elif tier == Tier.COMPLEX:
        distance = score - cfg.complex_threshold
        max_distance = 1.0 - cfg.complex_threshold
        confidence = min(1.0, distance / max_distance) if max_distance > 0 else 1.0
    else:
        mid = (cfg.simple_threshold + cfg.complex_threshold) / 2
        half_range = (cfg.complex_threshold - cfg.simple_threshold) / 2
        distance = half_range - abs(score - mid)
        confidence = min(1.0, distance / half_range) if half_range > 0 else 1.0

    # Detect borderline classifications (Phase 3)
    is_borderline = False
    borderline_alt: Tier | None = None

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
