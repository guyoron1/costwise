"""Cross-provider cost arbitrage: find the cheapest healthy model for a given tier."""

from __future__ import annotations

from dataclasses import dataclass, field

from costwise.core.health import ProviderHealthTracker
from costwise.core.models import CostEstimate, ModelInfo, Tier
from costwise.core.pricing import PricingRegistry


@dataclass
class ArbitrageResult:
    chosen: ModelInfo
    alternatives: list[ModelInfo]
    savings_vs_most_expensive: float
    fallback_chain: list[ModelInfo] = field(default_factory=list)
    skipped_unhealthy: list[str] = field(default_factory=list)


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
) -> ArbitrageResult | None:
    """Select the cheapest healthy model for a tier, with fallback chain."""
    candidates = registry.models_for_tier(tier)

    if enabled_providers:
        candidates = [m for m in candidates if m.provider in enabled_providers]
    if excluded_providers:
        candidates = [m for m in candidates if m.provider not in excluded_providers]
    if needs_tools:
        candidates = [m for m in candidates if m.supports_tools]
    if needs_vision:
        candidates = [m for m in candidates if m.supports_vision]

    if not candidates:
        return None

    def _cost(m: ModelInfo) -> float:
        return (
            estimated_input_tokens * m.input_cost_per_mtok / 1_000_000
            + estimated_output_tokens * m.output_cost_per_mtok / 1_000_000
        )

    ranked = sorted(candidates, key=_cost)
    most_expensive_cost = _cost(ranked[-1])

    skipped: list[str] = []
    if health_tracker:
        healthy = []
        for m in ranked:
            if health_tracker.is_healthy(m.provider):
                healthy.append(m)
            else:
                skipped.append(f"{m.provider}/{m.name}")

        if not healthy:
            healthy = ranked
            skipped = []
    else:
        healthy = ranked

    chosen = healthy[0]
    fallback = healthy[1:] if len(healthy) > 1 else []

    return ArbitrageResult(
        chosen=chosen,
        alternatives=ranked[1:],
        savings_vs_most_expensive=most_expensive_cost - _cost(chosen),
        fallback_chain=fallback,
        skipped_unhealthy=skipped,
    )


def estimate_for_model(
    model: ModelInfo,
    input_tokens: int,
    output_tokens: int,
) -> CostEstimate:
    return CostEstimate(
        model=model.name,
        provider=model.provider,
        input_cost_per_mtok=model.input_cost_per_mtok,
        output_cost_per_mtok=model.output_cost_per_mtok,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
    )
