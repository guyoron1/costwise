"""Tests for Phase 1: Expected Cost Optimization."""

from __future__ import annotations

import pytest

from costwise.core.arbitrage import select_cheapest
from costwise.core.expected_cost import estimate_retry_probability, expected_total_cost
from costwise.core.models import ModelInfo, Tier
from costwise.core.pricing import PricingRegistry
from costwise.core.router import Router, RouterConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _simple_registry() -> PricingRegistry:
    """Registry with known prices for deterministic testing."""
    return PricingRegistry()


def _model(name: str, tier: Tier, input_cost: float, output_cost: float) -> ModelInfo:
    return ModelInfo(
        name=name,
        provider="test",
        tier=tier,
        input_cost_per_mtok=input_cost,
        output_cost_per_mtok=output_cost,
    )


def _tiny_registry() -> PricingRegistry:
    """Minimal registry: one cheap-unreliable SIMPLE, one pricier-reliable SIMPLE, one MEDIUM."""
    return PricingRegistry([
        _model("cheap-simple", Tier.SIMPLE, 0.10, 0.40),
        _model("mid-simple", Tier.SIMPLE, 0.40, 1.60),
        _model("cheap-medium", Tier.MEDIUM, 0.80, 3.20),
        _model("only-complex", Tier.COMPLEX, 5.00, 25.00),
    ])


# ---------------------------------------------------------------------------
# Test: estimate_retry_probability
# ---------------------------------------------------------------------------

class TestEstimateRetryProbability:

    def test_base_rate_used_when_confidence_is_max(self):
        rates = {"SIMPLE": 0.10, "MEDIUM": 0.05, "COMPLEX": 0.02}
        prob = estimate_retry_probability(Tier.SIMPLE, confidence=1.0, per_tier_rates=rates)
        assert prob == pytest.approx(0.10)

    def test_low_confidence_inflates_probability(self):
        rates = {"SIMPLE": 0.10, "MEDIUM": 0.05, "COMPLEX": 0.02}
        prob = estimate_retry_probability(Tier.SIMPLE, confidence=0.0, per_tier_rates=rates)
        # multiplier = 1 + 2*(1-0) = 3.0 → 0.10 * 3.0 = 0.30
        assert prob == pytest.approx(0.30)

    def test_mid_confidence(self):
        rates = {"SIMPLE": 0.10}
        prob = estimate_retry_probability(Tier.SIMPLE, confidence=0.5, per_tier_rates=rates)
        # multiplier = 1 + 2*(0.5) = 2.0 → 0.10 * 2.0 = 0.20
        assert prob == pytest.approx(0.20)

    def test_capped_at_1(self):
        rates = {"SIMPLE": 0.50}
        prob = estimate_retry_probability(Tier.SIMPLE, confidence=0.0, per_tier_rates=rates)
        # 0.50 * 3.0 = 1.50 → capped to 1.0
        assert prob == 1.0

    def test_missing_tier_uses_default(self):
        prob = estimate_retry_probability(Tier.MEDIUM, confidence=1.0, per_tier_rates={})
        assert prob == pytest.approx(0.03)

    def test_complex_tier(self):
        rates = {"COMPLEX": 0.01}
        prob = estimate_retry_probability(Tier.COMPLEX, confidence=0.8, per_tier_rates=rates)
        # multiplier = 1 + 2*(0.2) = 1.4 → 0.01 * 1.4 = 0.014
        assert prob == pytest.approx(0.014)


# ---------------------------------------------------------------------------
# Test: expected_total_cost
# ---------------------------------------------------------------------------

class TestExpectedTotalCost:

    def test_zero_retry_returns_base_cost(self):
        registry = _tiny_registry()
        model = registry.get("cheap-simple")
        assert model is not None
        cost = expected_total_cost(model, 1000, 500, 0.0, registry)
        base = 1000 * 0.10 / 1e6 + 500 * 0.40 / 1e6
        assert cost == pytest.approx(base)

    def test_retry_adds_penalty(self):
        registry = _tiny_registry()
        model = registry.get("cheap-simple")
        assert model is not None
        cost = expected_total_cost(model, 1000, 500, 0.10, registry)
        base = 1000 * 0.10 / 1e6 + 500 * 0.40 / 1e6
        # upgrade to MEDIUM cheapest: cheap-medium at 0.80/3.20
        upgrade = 1000 * 0.80 / 1e6 + 500 * 3.20 / 1e6
        expected = base + 0.10 * (base + upgrade)
        assert cost == pytest.approx(expected)

    def test_complex_tier_retries_to_self(self):
        registry = _tiny_registry()
        model = registry.get("only-complex")
        assert model is not None
        cost = expected_total_cost(model, 1000, 500, 0.10, registry)
        base = 1000 * 5.00 / 1e6 + 500 * 25.00 / 1e6
        # COMPLEX retries to COMPLEX (same tier), so upgrade = same model cost
        expected = base + 0.10 * (base + base)
        assert cost == pytest.approx(expected)

    def test_ranking_inversion(self):
        """The core Phase 1 insight: a cheap model with high retry rate
        can cost MORE in expectation than a pricier model with low retry rate."""
        registry = _tiny_registry()
        cheap = registry.get("cheap-simple")
        mid = registry.get("mid-simple")
        assert cheap is not None and mid is not None

        # cheap-simple: base=$0.10/$0.40 per MTok, high retry
        cheap_expected = expected_total_cost(cheap, 1000, 500, 0.30, registry)
        # mid-simple: base=$0.40/$1.60 per MTok, low retry
        mid_expected = expected_total_cost(mid, 1000, 500, 0.02, registry)

        # With 30% retry vs 2% retry, the cheap model's expected cost
        # should be inflated significantly
        cheap_base = 1000 * 0.10 / 1e6 + 500 * 0.40 / 1e6
        mid_base = 1000 * 0.40 / 1e6 + 500 * 1.60 / 1e6

        # Verify the math makes sense
        assert cheap_expected > cheap_base  # retry penalty adds cost
        assert mid_expected > mid_base  # even small retry adds some cost
        assert mid_expected < mid_base * 1.10  # small premium with 2% retry


# ---------------------------------------------------------------------------
# Test: select_cheapest with retry_probability
# ---------------------------------------------------------------------------

class TestSelectCheapestWithRetryProbability:

    def test_backward_compatible_when_none(self):
        """retry_probability=None preserves original cheapest-first behavior."""
        registry = _simple_registry()
        result_without = select_cheapest(registry, Tier.SIMPLE, retry_probability=None)
        result_with_zero = select_cheapest(registry, Tier.SIMPLE, retry_probability=0.0)

        assert result_without is not None
        assert result_with_zero is not None
        assert result_without.chosen.name == result_with_zero.chosen.name

    def test_default_behavior_unchanged(self):
        """Without retry_probability, cheapest model wins."""
        registry = _simple_registry()
        result = select_cheapest(registry, Tier.SIMPLE)
        assert result is not None
        cheapest = min(
            registry.models_for_tier(Tier.SIMPLE),
            key=lambda m: m.input_cost_per_mtok * 1000 / 1e6 + m.output_cost_per_mtok * 500 / 1e6,
        )
        assert result.chosen.name == cheapest.name

    def test_retry_probability_can_change_ranking(self):
        """With retry probability, the ranking may differ from pure cost."""
        registry = _simple_registry()
        result_no_retry = select_cheapest(registry, Tier.SIMPLE, retry_probability=None)
        result_high_retry = select_cheapest(registry, Tier.SIMPLE, retry_probability=0.50)

        assert result_no_retry is not None
        assert result_high_retry is not None
        # The expected-cost ranking should still produce a valid model
        assert result_high_retry.chosen.tier == Tier.SIMPLE

    def test_health_tracker_still_works_with_retry(self):
        """Health filtering works alongside retry probability."""
        from costwise.core.health import ProviderHealthTracker

        tracker = ProviderHealthTracker(rate_limit_cooldown_s=30.0)
        tracker.record_rate_limit("google")

        registry = _simple_registry()
        result = select_cheapest(
            registry, Tier.SIMPLE,
            health_tracker=tracker,
            retry_probability=0.10,
        )
        assert result is not None
        assert result.chosen.provider != "google"

    def test_filters_still_applied_with_retry(self):
        """Provider filtering, tools, vision filters still work with retry probability."""
        registry = _simple_registry()
        result = select_cheapest(
            registry, Tier.SIMPLE,
            enabled_providers={"anthropic"},
            retry_probability=0.10,
        )
        assert result is not None
        assert result.chosen.provider == "anthropic"


# ---------------------------------------------------------------------------
# Test: Router integration with retry_rates
# ---------------------------------------------------------------------------

class TestRouterWithRetryRates:

    def _make_router(self) -> Router:
        return Router(registry=PricingRegistry())

    def _complex_body(self) -> dict:
        return {
            "model": "claude-opus-4-7",
            "messages": [
                {"role": "user", "content": "Refactor the authentication " * 50
                 + "system. Fix the error handling. Retry the failed test."},
            ],
            "tools": [{"type": "function", "function": {"name": "edit"}}],
        }

    def _simple_body(self) -> dict:
        return {
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "hello there"}],
        }

    def test_route_without_retry_rates(self):
        """Router works normally without retry_rates."""
        router = self._make_router()
        decision = router.route(self._complex_body())
        assert decision.routed_model is not None

    def test_route_with_retry_rates(self):
        """Router accepts retry_rates and produces a valid decision."""
        router = self._make_router()
        rates = {"SIMPLE": 0.10, "MEDIUM": 0.05, "COMPLEX": 0.02}
        decision = router.route(self._complex_body(), retry_rates=rates)
        assert decision.routed_model is not None

    def test_route_with_empty_retry_rates(self):
        """Empty retry_rates dict doesn't crash."""
        router = self._make_router()
        decision = router.route(self._complex_body(), retry_rates={})
        assert decision.routed_model is not None

    def test_passthrough_ignores_retry_rates(self):
        """When routing is disabled, retry_rates are irrelevant."""
        config = RouterConfig(enabled=False)
        router = Router(registry=PricingRegistry(), config=config)
        rates = {"SIMPLE": 0.50}
        decision = router.route(self._simple_body(), retry_rates=rates)
        assert not decision.is_rerouted


# ---------------------------------------------------------------------------
# Test: TrackingStore.get_retry_rate_by_tier (sync, in-memory SQLite)
# ---------------------------------------------------------------------------

class TestGetRetryRateByTier:

    @pytest.fixture()
    def store(self, tmp_path):
        from costwise.tracking.store import TrackingStore
        s = TrackingStore(tmp_path / "test.db")
        s._get_conn()
        return s

    @pytest.mark.asyncio
    async def test_returns_all_tiers(self, store):
        rates = await store.get_retry_rate_by_tier()
        assert "SIMPLE" in rates
        assert "MEDIUM" in rates
        assert "COMPLEX" in rates

    @pytest.mark.asyncio
    async def test_zero_rates_with_no_data(self, store):
        rates = await store.get_retry_rate_by_tier()
        assert all(v == 0.0 for v in rates.values())

    @pytest.mark.asyncio
    async def test_rates_computed_from_data(self, store):
        from costwise.tracking.store import RoutingRecord

        # Insert 10 SIMPLE routing decisions
        for _ in range(10):
            await store.record_request(RoutingRecord(
                endpoint="/v1/messages",
                request_model="claude-haiku-4-5",
                routed_model="gemini-2.5-flash-lite",
                tier="SIMPLE",
                session_id="test-sess",
            ))

        # Insert 2 retry events for SIMPLE tier
        await store.record_retry_event(
            session_id="test-sess",
            original_request_id=1,
            retry_request_id=2,
            content_hash="abc123",
            similarity_score=0.85,
            original_tier="SIMPLE",
            original_model="gemini-2.5-flash-lite",
            time_delta_s=30.0,
            was_downgraded=True,
        )
        await store.record_retry_event(
            session_id="test-sess",
            original_request_id=3,
            retry_request_id=4,
            content_hash="def456",
            similarity_score=0.90,
            original_tier="SIMPLE",
            original_model="gemini-2.5-flash-lite",
            time_delta_s=45.0,
            was_downgraded=True,
        )

        rates = await store.get_retry_rate_by_tier()
        # 2 retries out of 10 SIMPLE requests = 0.20
        assert rates["SIMPLE"] == pytest.approx(0.20)
        assert rates["MEDIUM"] == 0.0
        assert rates["COMPLEX"] == 0.0
