"""Bundled model pricing registry.

Prices are in USD per million tokens. Update this file when providers change pricing.
Last updated: 2026-06-30.
"""

from __future__ import annotations

from costwise.core.models import ModelInfo, Tier

MODELS: list[ModelInfo] = [
    # ── Anthropic ──────────────────────────────────────────
    ModelInfo(
        name="claude-opus-4-7",
        provider="anthropic",
        tier=Tier.COMPLEX,
        input_cost_per_mtok=5.0,
        output_cost_per_mtok=25.0,
        context_window=200_000,
        max_output_tokens=32_000,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="claude-sonnet-4-6",
        provider="anthropic",
        tier=Tier.MEDIUM,
        input_cost_per_mtok=3.0,
        output_cost_per_mtok=15.0,
        context_window=200_000,
        max_output_tokens=16_000,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="claude-haiku-4-5",
        provider="anthropic",
        tier=Tier.SIMPLE,
        input_cost_per_mtok=1.0,
        output_cost_per_mtok=5.0,
        context_window=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_vision=True,
    ),
    # ── OpenAI ─────────────────────────────────────────────
    ModelInfo(
        name="gpt-5",
        provider="openai",
        tier=Tier.COMPLEX,
        input_cost_per_mtok=1.25,
        output_cost_per_mtok=10.0,
        context_window=128_000,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="gpt-4.1",
        provider="openai",
        tier=Tier.MEDIUM,
        input_cost_per_mtok=2.0,
        output_cost_per_mtok=8.0,
        context_window=1_000_000,
        max_output_tokens=32_768,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="gpt-4.1-mini",
        provider="openai",
        tier=Tier.SIMPLE,
        input_cost_per_mtok=0.40,
        output_cost_per_mtok=1.60,
        context_window=1_000_000,
        max_output_tokens=32_768,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="gpt-4.1-nano",
        provider="openai",
        tier=Tier.SIMPLE,
        input_cost_per_mtok=0.10,
        output_cost_per_mtok=0.40,
        context_window=1_000_000,
        max_output_tokens=32_768,
        supports_tools=True,
        supports_vision=False,
    ),
    # ── Google ─────────────────────────────────────────────
    ModelInfo(
        name="gemini-2.5-pro",
        provider="google",
        tier=Tier.COMPLEX,
        input_cost_per_mtok=1.25,
        output_cost_per_mtok=10.0,
        context_window=1_000_000,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="gemini-2.5-flash",
        provider="google",
        tier=Tier.MEDIUM,
        input_cost_per_mtok=0.30,
        output_cost_per_mtok=2.50,
        context_window=1_000_000,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
    ),
    ModelInfo(
        name="gemini-2.5-flash-lite",
        provider="google",
        tier=Tier.SIMPLE,
        input_cost_per_mtok=0.10,
        output_cost_per_mtok=0.40,
        context_window=1_000_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_vision=True,
    ),
]


class PricingRegistry:
    """Lookup models by name, tier, or provider."""

    def __init__(self, models: list[ModelInfo] | None = None) -> None:
        self._models = models or MODELS
        self._by_name: dict[str, ModelInfo] = {}
        self._by_tier: dict[Tier, list[ModelInfo]] = {t: [] for t in Tier}
        self._by_provider: dict[str, list[ModelInfo]] = {}
        for m in self._models:
            self._by_name[m.name] = m
            self._by_tier[m.tier].append(m)
            self._by_provider.setdefault(m.provider, []).append(m)

    def get(self, model_name: str) -> ModelInfo | None:
        """Exact match, then prefix match (e.g. 'claude-sonnet-4' matches 'claude-sonnet-4-6')."""
        if model_name in self._by_name:
            return self._by_name[model_name]
        for name, info in self._by_name.items():
            if name.startswith(model_name) or model_name.startswith(name):
                return info
        return None

    def models_for_tier(self, tier: Tier) -> list[ModelInfo]:
        return self._by_tier.get(tier, [])

    def cheapest_for_tier(
        self,
        tier: Tier,
        providers: set[str] | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
    ) -> ModelInfo | None:
        candidates = self._by_tier.get(tier, [])
        if providers:
            candidates = [m for m in candidates if m.provider in providers]
        if needs_tools:
            candidates = [m for m in candidates if m.supports_tools]
        if needs_vision:
            candidates = [m for m in candidates if m.supports_vision]
        if not candidates:
            return None
        return min(candidates, key=lambda m: m.blended_cost_per_mtok)

    def estimate_cost(self, model_name: str, input_tokens: int, output_tokens: int) -> float | None:
        info = self.get(model_name)
        if not info:
            return None
        return (
            input_tokens * info.input_cost_per_mtok / 1_000_000
            + output_tokens * info.output_cost_per_mtok / 1_000_000
        )

    @property
    def all_models(self) -> list[ModelInfo]:
        return list(self._models)

    @property
    def providers(self) -> list[str]:
        return list(self._by_provider.keys())
