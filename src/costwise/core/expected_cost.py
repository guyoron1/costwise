"""Expected cost calculator: accounts for retry risk in model selection."""

from __future__ import annotations

from costwise.core.models import ModelInfo, Tier
from costwise.core.pricing import PricingRegistry

_UPGRADE_TIER = {Tier.SIMPLE: Tier.MEDIUM, Tier.MEDIUM: Tier.COMPLEX}


def estimate_retry_probability(
    tier: Tier,
    confidence: float,
    per_tier_rates: dict[str, float],
) -> float:
    """Estimate P(retry) for a routing decision.

    Args:
        tier: The classified tier.
        confidence: Classifier confidence (0.0-1.0). Lower confidence -> higher retry risk.
        per_tier_rates: Historical retry rates per tier from the tracking store.
            Keys are tier names ("SIMPLE", "MEDIUM", "COMPLEX"), values are rates (0.0-1.0).

    Returns:
        Estimated retry probability (0.0-1.0).
    """
    base_rate = per_tier_rates.get(tier.value, 0.03)

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

    expected_cost = base_cost + P(retry) * (base_cost + cheapest_upgrade_cost)

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

    upgrade_tier = _UPGRADE_TIER.get(model.tier, model.tier)
    upgrade_candidates = registry.models_for_tier(upgrade_tier)

    if not upgrade_candidates:
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
