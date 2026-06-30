"""Tests for health-aware arbitrage and router budget integration."""

from __future__ import annotations

import pytest

from costwise.config.schema import BudgetConfig
from costwise.core.arbitrage import select_cheapest
from costwise.core.budget import BudgetAction, BudgetEnforcer
from costwise.core.health import ProviderHealthTracker
from costwise.core.models import ModelInfo, SignalBundle, Tier
from costwise.core.pricing import PricingRegistry
from costwise.core.router import Router, RouterConfig


class TestHealthAwareArbitrage:

    def test_skips_unhealthy_provider(self):
        tracker = ProviderHealthTracker(rate_limit_cooldown_s=30.0)
        tracker.record_rate_limit("openai")

        result = select_cheapest(
            PricingRegistry(),
            Tier.SIMPLE,
            health_tracker=tracker,
        )
        assert result is not None
        assert result.chosen.provider != "openai"
        assert any("openai" in s for s in result.skipped_unhealthy)

    def test_all_unhealthy_falls_back_to_all(self):
        tracker = ProviderHealthTracker(rate_limit_cooldown_s=30.0)
        tracker.record_rate_limit("anthropic")
        tracker.record_rate_limit("openai")
        tracker.record_rate_limit("google")

        result = select_cheapest(
            PricingRegistry(),
            Tier.SIMPLE,
            health_tracker=tracker,
        )
        assert result is not None
        assert len(result.skipped_unhealthy) == 0

    def test_fallback_chain_excludes_chosen(self):
        result = select_cheapest(
            PricingRegistry(),
            Tier.SIMPLE,
            health_tracker=ProviderHealthTracker(),
        )
        assert result is not None
        assert result.chosen.name not in [m.name for m in result.fallback_chain]

    def test_fallback_chain_ordered_by_cost(self):
        result = select_cheapest(
            PricingRegistry(),
            Tier.SIMPLE,
            estimated_input_tokens=1000,
            estimated_output_tokens=500,
            health_tracker=ProviderHealthTracker(),
        )
        assert result is not None
        if len(result.fallback_chain) >= 2:
            costs = [
                m.input_cost_per_mtok * 1000 / 1e6 + m.output_cost_per_mtok * 500 / 1e6
                for m in result.fallback_chain
            ]
            assert costs == sorted(costs)

    def test_healthy_provider_preferred(self):
        tracker = ProviderHealthTracker(rate_limit_cooldown_s=30.0)
        tracker.record_rate_limit("google")

        result = select_cheapest(
            PricingRegistry(),
            Tier.COMPLEX,
            health_tracker=tracker,
        )
        assert result is not None
        assert result.chosen.provider != "google"


class TestRouterBudgetIntegration:

    def _make_router(self, budget_config: BudgetConfig | None = None) -> Router:
        budget = BudgetEnforcer(budget_config or BudgetConfig())
        return Router(
            registry=PricingRegistry(),
            health_tracker=ProviderHealthTracker(),
            budget_enforcer=budget,
        )

    def _complex_body(self) -> dict:
        return {
            "model": "claude-opus-4-7",
            "messages": [
                {"role": "user", "content": "Refactor the authentication " * 50
                 + "system. Fix the error handling. Retry the failed test."},
            ],
            "tools": [{"type": "function", "function": {"name": "edit"}}],
        }

    def test_budget_allows_normal_request(self):
        router = self._make_router(BudgetConfig(max_session_usd=100.0))
        decision = router.route(self._complex_body())
        assert decision.budget_action == "allow"

    def test_budget_warns_near_limit(self):
        config = BudgetConfig(max_session_usd=10.0, warning_threshold_pct=80.0)
        router = self._make_router(config)
        router.budget_enforcer.record_spend(9.0)
        decision = router.route(self._complex_body())
        assert decision.budget_action == "warn"
        assert decision.budget_warning != ""

    def test_budget_downgrades_when_exceeded(self):
        config = BudgetConfig(max_session_usd=1.0, auto_downgrade=True)
        router = self._make_router(config)
        router.budget_enforcer.record_spend(2.0)
        decision = router.route(self._complex_body())
        assert decision.budget_action == "downgrade"
        assert decision.tier != Tier.COMPLEX

    def test_budget_blocks_simple_when_exceeded(self):
        config = BudgetConfig(max_session_usd=1.0, auto_downgrade=True)
        router = self._make_router(config)
        router.budget_enforcer.record_spend(2.0)
        body = {
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "hello"}],
        }
        decision = router.route(body)
        assert decision.budget_action == "block"

    def test_fallback_chain_in_decision(self):
        router = self._make_router()
        decision = router.route(self._complex_body())
        if decision.is_rerouted:
            assert isinstance(decision.fallback_chain, list)

    def test_routing_without_budget(self):
        router = Router(registry=PricingRegistry())
        decision = router.route(self._complex_body())
        assert decision.budget_action == "allow"
