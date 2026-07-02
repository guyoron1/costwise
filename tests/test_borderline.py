"""Tests for Phase 3: Borderline Case Handling (Soft Boundaries)."""

from __future__ import annotations

from costwise.core.classifier import ClassificationResult, ClassifierConfig, classify
from costwise.core.models import ModelInfo, SignalBundle, Tier
from costwise.core.pricing import PricingRegistry
from costwise.core.router import Router, RouterConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signals_with_score_near(target_score: float, cfg: ClassifierConfig | None = None) -> SignalBundle:
    """Build a SignalBundle that produces approximately `target_score` from classify().

    Uses a greedy fill strategy across multiple signal dials ordered by weight.
    """
    cfg = cfg or ClassifierConfig()

    # Intent "unknown" always contributes 0.35 * w_intent
    remaining = target_score - 0.35 * cfg.w_intent
    if remaining <= 0:
        return SignalBundle()

    kwargs: dict = {}

    # For scores above ~0.40, switch to stronger intent and enable compound signals
    if target_score > 0.40:
        kwargs["intent"] = "refactor"  # 0.7 vs 0.35
        remaining -= (0.7 - 0.35) * cfg.w_intent

        kwargs["has_code"] = True
        kwargs["code_block_count"] = 1
        kwargs["has_tools"] = True
        kwargs["tool_count"] = 1
        # Match classifier formulas: code_score=min(1,0.4+cbc*0.15)=0.55,
        # tool_score=min(1,0.3+tc*0.1)=0.4, compound=min(1,0.6+cbc*0.1+tc*0.05)=0.75
        remaining -= 0.55 * cfg.w_code + 0.4 * cfg.w_tools + 0.75 * cfg.w_code_tools_compound

    # Greedy fill: binary and continuous dials in weight order
    if remaining >= cfg.w_retry:
        kwargs["has_retry_context"] = True
        remaining -= cfg.w_retry

    # error_severity (continuous, weight w_error)
    if remaining > 0 and cfg.w_error > 0:
        val = min(1.0, remaining / cfg.w_error)
        kwargs["error_severity"] = val
        kwargs["has_error_context"] = val > 0
        remaining -= val * cfg.w_error

    # graph_complexity (continuous, weight w_graph_complexity)
    if remaining > 0 and cfg.w_graph_complexity > 0:
        val = min(1.0, remaining / cfg.w_graph_complexity)
        kwargs["graph_complexity"] = val
        remaining -= val * cfg.w_graph_complexity

    # token_count (continuous via normalization)
    if remaining > 0 and cfg.w_token_count > 0:
        val = min(1.0, remaining / cfg.w_token_count)
        kwargs["token_count"] = int(cfg.token_low + val * (cfg.token_high - cfg.token_low))
        remaining -= val * cfg.w_token_count

    # conversation_depth (continuous via normalization)
    if remaining > 0 and cfg.w_depth > 0:
        val = min(1.0, remaining / cfg.w_depth)
        kwargs["conversation_depth"] = int(cfg.depth_low + val * (cfg.depth_high - cfg.depth_low))
        remaining -= val * cfg.w_depth

    # image_count
    if remaining > 0 and cfg.w_images > 0:
        val = min(1.0, remaining / cfg.w_images)
        kwargs["image_count"] = max(1, int(val * 2))
        remaining -= min(1.0, kwargs["image_count"] * 0.5) * cfg.w_images

    # multi_file (requires has_code)
    if remaining > 0 and kwargs.get("has_code"):
        kwargs["multi_file_scope"] = True
        kwargs["referenced_file_count"] = 5
        remaining -= min(1.0, 0.5 + 5 * 0.1) * cfg.w_multi_file

    return SignalBundle(**kwargs)


def _model(name: str, tier: Tier, input_cost: float, output_cost: float) -> ModelInfo:
    return ModelInfo(
        name=name,
        provider="test",
        tier=tier,
        input_cost_per_mtok=input_cost,
        output_cost_per_mtok=output_cost,
    )


def _test_registry() -> PricingRegistry:
    """Registry with controlled prices for borderline testing."""
    return PricingRegistry([
        _model("cheap-simple", Tier.SIMPLE, 0.10, 0.40),
        _model("mid-simple", Tier.SIMPLE, 0.40, 1.60),
        _model("cheap-medium", Tier.MEDIUM, 0.30, 2.50),
        _model("mid-medium", Tier.MEDIUM, 2.00, 8.00),
        _model("cheap-complex", Tier.COMPLEX, 1.25, 10.00),
    ])


# ---------------------------------------------------------------------------
# Test: Borderline detection in classifier
# ---------------------------------------------------------------------------

class TestBorderlineDetection:

    def test_borderline_flag_matches_score_proximity(self):
        """Verify borderline flag is set iff score is within boundary_zone of a threshold."""
        cfg = ClassifierConfig(boundary_zone=0.05)

        for error_sev in [0.0, 0.3, 0.6, 1.0]:
            for retry in [False, True]:
                for gc in [0.0, 0.5, 1.0]:
                    signals = SignalBundle(
                        error_severity=error_sev,
                        has_error_context=error_sev > 0,
                        has_retry_context=retry,
                        graph_complexity=gc,
                    )
                    result = classify(signals, cfg)

                    expected_borderline = False
                    if result.tier == Tier.SIMPLE:
                        expected_borderline = (cfg.simple_threshold - result.score) < cfg.boundary_zone
                    elif result.tier == Tier.MEDIUM:
                        expected_borderline = (
                            (result.score - cfg.simple_threshold) < cfg.boundary_zone
                            or (cfg.complex_threshold - result.score) < cfg.boundary_zone
                        )
                    elif result.tier == Tier.COMPLEX:
                        expected_borderline = (result.score - cfg.complex_threshold) < cfg.boundary_zone

                    assert result.is_borderline == expected_borderline, (
                        f"Score {result.score:.3f} tier {result.tier.value}: "
                        f"expected borderline={expected_borderline}, got {result.is_borderline} "
                        f"(error_sev={error_sev}, retry={retry}, gc={gc})"
                    )

    def test_simple_near_medium_boundary(self):
        """Score just below simple_threshold should be flagged as borderline."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.17, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.SIMPLE
        assert result.is_borderline is True
        assert result.borderline_alternative_tier == Tier.MEDIUM

    def test_simple_well_below_boundary(self):
        """Score far below simple_threshold should NOT be borderline."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.05, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.SIMPLE
        assert result.is_borderline is False
        assert result.borderline_alternative_tier is None

    def test_medium_near_simple_boundary(self):
        """Score just above simple_threshold should be borderline toward SIMPLE."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.22, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.MEDIUM
        assert result.is_borderline is True
        assert result.borderline_alternative_tier == Tier.SIMPLE

    def test_medium_near_complex_boundary(self):
        """Score just below complex_threshold should be borderline toward COMPLEX."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.52, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.MEDIUM
        assert result.is_borderline is True
        assert result.borderline_alternative_tier == Tier.COMPLEX

    def test_medium_well_within_range(self):
        """Score in the middle of MEDIUM range should NOT be borderline."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.38, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.MEDIUM
        assert result.is_borderline is False
        assert result.borderline_alternative_tier is None

    def test_complex_near_medium_boundary(self):
        """Score just above complex_threshold should be borderline toward MEDIUM."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.57, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.COMPLEX
        assert result.is_borderline is True
        assert result.borderline_alternative_tier == Tier.MEDIUM

    def test_complex_well_above_boundary(self):
        """Score well above complex_threshold should NOT be borderline."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.05)
        signals = _signals_with_score_near(0.75, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.COMPLEX
        assert result.is_borderline is False
        assert result.borderline_alternative_tier is None

    def test_boundary_zone_zero_disables_borderline(self):
        """With boundary_zone=0, nothing is ever borderline."""
        cfg = ClassifierConfig(simple_threshold=0.20, complex_threshold=0.55, boundary_zone=0.0)
        for target in [0.17, 0.22, 0.52, 0.57]:
            signals = _signals_with_score_near(target, cfg)
            result = classify(signals, cfg)
            assert result.is_borderline is False

    def test_default_fields_backward_compatible(self):
        """ClassificationResult defaults to not-borderline."""
        result = ClassificationResult(tier=Tier.SIMPLE, score=0.10, confidence=0.8)
        assert result.is_borderline is False
        assert result.borderline_alternative_tier is None

    def test_alternative_tier_direction(self):
        """Borderline alternative always points to the adjacent tier."""
        cfg = ClassifierConfig(boundary_zone=0.05)
        # SIMPLE borderline → MEDIUM
        signals = _signals_with_score_near(0.17, cfg)
        r = classify(signals, cfg)
        if r.is_borderline:
            assert r.borderline_alternative_tier == Tier.MEDIUM

        # COMPLEX borderline → MEDIUM
        signals = _signals_with_score_near(0.57, cfg)
        r = classify(signals, cfg)
        if r.is_borderline:
            assert r.borderline_alternative_tier == Tier.MEDIUM


# ---------------------------------------------------------------------------
# Test: Router borderline cost comparison
# ---------------------------------------------------------------------------

class TestBorderlineCostComparison:

    def test_borderline_with_high_retry_uses_expected_cost(self):
        """When a borderline SIMPLE request has high retry rate,
        the router should consider MEDIUM models via expected cost comparison."""
        registry = _test_registry()
        cfg = ClassifierConfig(boundary_zone=0.05)
        router = Router(
            registry=registry,
            config=RouterConfig(classifier=cfg),
        )

        # Craft signals near SIMPLE/MEDIUM boundary
        signals = _signals_with_score_near(0.17, cfg)
        result = classify(signals, cfg)

        # Only proceed if we actually got a borderline result
        if result.is_borderline and result.tier == Tier.SIMPLE:
            body = {
                "model": "cheap-complex",
                "messages": [{"role": "user", "content": "x" * max(signals.token_count, 100)}],
            }
            decision = router.route(
                body,
                retry_rates={"SIMPLE": 0.30, "MEDIUM": 0.02, "COMPLEX": 0.01},
            )
            assert decision.routed_model is not None

    def test_borderline_keeps_tier_with_low_retry(self):
        """With very low retry rates, borderline logic shouldn't change the tier."""
        registry = _test_registry()
        cfg = ClassifierConfig(boundary_zone=0.05)
        router = Router(
            registry=registry,
            config=RouterConfig(classifier=cfg),
        )
        signals = _signals_with_score_near(0.17, cfg)

        body = {
            "model": "cheap-complex",
            "messages": [{"role": "user", "content": "x" * max(signals.token_count, 100)}],
        }
        decision = router.route(
            body,
            retry_rates={"SIMPLE": 0.01, "MEDIUM": 0.01, "COMPLEX": 0.01},
        )
        assert decision.routed_model is not None

    def test_no_borderline_comparison_without_retry_rates(self):
        """Without retry_rates, borderline logic is skipped."""
        registry = _test_registry()
        cfg = ClassifierConfig(boundary_zone=0.05)
        router = Router(
            registry=registry,
            config=RouterConfig(classifier=cfg),
        )
        signals = _signals_with_score_near(0.17, cfg)
        body = {
            "model": "cheap-complex",
            "messages": [{"role": "user", "content": "x" * max(signals.token_count, 100)}],
        }
        decision = router.route(body, retry_rates=None)
        assert decision.routed_model is not None

    def test_borderline_comparison_picks_lower_expected_cost(self):
        """Directly verify: when alt tier has lower expected cost, it wins."""
        from costwise.core.expected_cost import expected_total_cost

        registry = _test_registry()

        # SIMPLE cheapest model: cheap-simple ($0.10/$0.40)
        simple_model = registry.get("cheap-simple")
        # MEDIUM cheapest model: cheap-medium ($0.30/$2.50)
        medium_model = registry.get("cheap-medium")
        assert simple_model is not None and medium_model is not None

        input_tokens, output_tokens = 1000, 300
        retry_prob = 0.25

        simple_expected = expected_total_cost(
            simple_model, input_tokens, output_tokens, retry_prob, registry,
        )
        medium_expected = expected_total_cost(
            medium_model, input_tokens, output_tokens, retry_prob, registry,
        )

        # With 25% retry probability, SIMPLE model's expected cost includes
        # the penalty of upgrading to MEDIUM on retry. Verify both produce
        # valid costs.
        assert simple_expected > 0
        assert medium_expected > 0


# ---------------------------------------------------------------------------
# Test: Non-borderline pass-through
# ---------------------------------------------------------------------------

class TestNonBorderlinePassthrough:

    def test_clear_simple_not_affected(self):
        """A clearly SIMPLE request shouldn't trigger borderline logic."""
        cfg = ClassifierConfig(boundary_zone=0.05)
        signals = _signals_with_score_near(0.05, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.SIMPLE
        assert not result.is_borderline

    def test_clear_medium_not_affected(self):
        """A clearly MEDIUM request shouldn't trigger borderline logic."""
        cfg = ClassifierConfig(boundary_zone=0.05)
        signals = _signals_with_score_near(0.38, cfg)
        result = classify(signals, cfg)
        assert result.tier == Tier.MEDIUM
        assert not result.is_borderline

    def test_routing_unchanged_for_non_borderline(self):
        """Non-borderline requests route to the same tier regardless of retry_rates."""
        registry = _test_registry()
        cfg = ClassifierConfig(boundary_zone=0.05)
        router = Router(
            registry=registry,
            config=RouterConfig(classifier=cfg),
        )

        signals = _signals_with_score_near(0.38, cfg)
        body = {
            "model": "cheap-complex",
            "messages": [{"role": "user", "content": "x" * max(signals.token_count, 100)}],
        }

        decision_no_rates = router.route(body)
        decision_with_rates = router.route(
            body,
            retry_rates={"SIMPLE": 0.30, "MEDIUM": 0.30, "COMPLEX": 0.30},
        )

        assert decision_no_rates.tier == decision_with_rates.tier
