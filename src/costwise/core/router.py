"""Main routing orchestrator: signals → classifier → budget check → arbitrage → RoutingDecision."""

from __future__ import annotations

from dataclasses import dataclass, field

from costwise.core.arbitrage import estimate_for_model, select_cheapest
from costwise.core.budget import BudgetAction, BudgetEnforcer
from costwise.core.classifier import ClassificationResult, ClassifierConfig, classify
from costwise.core.health import ProviderHealthTracker
from costwise.core.models import RoutingDecision, SignalBundle, Tier
from costwise.core.pricing import PricingRegistry
from costwise.core.signals import extract_signals
from costwise.graph.loader import CodeGraph


_PROVIDER_API_BASES: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "google": "https://generativelanguage.googleapis.com",
}


@dataclass
class RouterConfig:
    enabled: bool = True
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    enabled_providers: set[str] = field(default_factory=lambda: {"anthropic", "openai", "google"})
    excluded_providers: set[str] = field(default_factory=set)
    min_confidence: float = 0.1
    provider_api_bases: dict[str, str] = field(default_factory=lambda: dict(_PROVIDER_API_BASES))
    default_output_ratio: float = 0.3


class Router:
    def __init__(
        self,
        registry: PricingRegistry | None = None,
        config: RouterConfig | None = None,
        health_tracker: ProviderHealthTracker | None = None,
        budget_enforcer: BudgetEnforcer | None = None,
    ) -> None:
        self._registry = registry or PricingRegistry()
        self._config = config or RouterConfig()
        self._health = health_tracker
        self._budget = budget_enforcer

    @property
    def config(self) -> RouterConfig:
        return self._config

    @property
    def registry(self) -> PricingRegistry:
        return self._registry

    @property
    def health_tracker(self) -> ProviderHealthTracker | None:
        return self._health

    @property
    def budget_enforcer(self) -> BudgetEnforcer | None:
        return self._budget

    def route(self, request_body: dict, graph: CodeGraph | None = None) -> RoutingDecision:
        original_model = request_body.get("model", "unknown")
        original_info = self._registry.get(original_model)

        if not self._config.enabled or not original_info:
            return self._passthrough(original_model, reason="routing disabled or model unknown")

        signals = extract_signals(request_body)

        if graph:
            from costwise.graph.relevance import compute_graph_complexity

            all_text = " ".join(
                m.get("content", "") for m in request_body.get("messages", [])
                if isinstance(m.get("content"), str)
            )
            signals.graph_complexity = compute_graph_complexity(graph, all_text)

        classification = classify(signals, self._config.classifier)

        if classification.confidence < self._config.min_confidence:
            return self._passthrough(
                original_model,
                reason=f"low confidence ({classification.confidence:.2f})",
            )

        effective_tier = classification.tier
        budget_action = BudgetAction.ALLOW
        budget_warning = ""

        if self._budget:
            budget_result = self._budget.check(effective_tier)
            budget_action = budget_result.action

            if budget_action == BudgetAction.BLOCK:
                return self._passthrough(
                    original_model,
                    tier=effective_tier,
                    confidence=classification.confidence,
                    reason=f"budget blocked: {budget_result.reason}",
                    budget_action=budget_action.value,
                    budget_warning=budget_result.reason,
                )

            if budget_action == BudgetAction.DOWNGRADE and budget_result.downgrade_to:
                effective_tier = budget_result.downgrade_to
                budget_warning = budget_result.reason

            if budget_action == BudgetAction.WARN:
                budget_warning = budget_result.reason

        if effective_tier == original_info.tier and budget_action == BudgetAction.ALLOW:
            return self._passthrough(
                original_model,
                tier=effective_tier,
                confidence=classification.confidence,
                reason=f"already optimal tier: {classification.reason}",
            )

        return self._reroute(
            original_model, original_info.tier, signals, classification,
            effective_tier=effective_tier,
            budget_action=budget_action.value,
            budget_warning=budget_warning,
        )

    def route_from_signals(
        self,
        original_model: str,
        signals: SignalBundle,
    ) -> RoutingDecision:
        """Route using pre-extracted signals (useful for testing)."""
        original_info = self._registry.get(original_model)
        if not self._config.enabled or not original_info:
            return self._passthrough(original_model, reason="routing disabled or model unknown")

        classification = classify(signals, self._config.classifier)

        if classification.tier == original_info.tier:
            return self._passthrough(
                original_model,
                tier=classification.tier,
                confidence=classification.confidence,
                reason=f"already optimal tier: {classification.reason}",
            )

        return self._reroute(original_model, original_info.tier, signals, classification)

    def _reroute(
        self,
        original_model: str,
        _original_tier: Tier,
        signals: SignalBundle,
        classification: ClassificationResult,
        *,
        effective_tier: Tier | None = None,
        budget_action: str = "allow",
        budget_warning: str = "",
    ) -> RoutingDecision:
        tier = effective_tier or classification.tier
        needs_tools = signals.has_tools
        needs_vision = signals.image_count > 0

        result = select_cheapest(
            self._registry,
            tier,
            estimated_input_tokens=max(signals.token_count, 100),
            estimated_output_tokens=max(int(signals.token_count * self._config.default_output_ratio), 50),
            enabled_providers=self._config.enabled_providers or None,
            excluded_providers=self._config.excluded_providers or None,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            health_tracker=self._health,
        )

        if not result:
            return self._passthrough(
                original_model,
                tier=tier,
                confidence=classification.confidence,
                reason=f"no model available for tier {tier.value}",
            )

        chosen = result.chosen
        input_tokens = max(signals.token_count, 100)
        output_tokens = max(int(input_tokens * self._config.default_output_ratio), 50)

        cost_est = estimate_for_model(chosen, input_tokens, output_tokens)

        original_info = self._registry.get(original_model)
        baseline_est = None
        if original_info:
            baseline_est = estimate_for_model(original_info, input_tokens, output_tokens)

        api_base = self._config.provider_api_bases.get(chosen.provider, "")

        fallback_names = [m.name for m in result.fallback_chain[:3]]

        return RoutingDecision(
            original_model=original_model,
            routed_model=chosen.name,
            tier=tier,
            provider=chosen.provider,
            api_base=api_base,
            confidence=classification.confidence,
            reason=classification.reason,
            cost_estimate=cost_est,
            baseline_cost=baseline_est,
            budget_action=budget_action,
            budget_warning=budget_warning,
            fallback_chain=fallback_names,
        )

    def _passthrough(
        self,
        model: str,
        *,
        tier: Tier | None = None,
        confidence: float = 1.0,
        reason: str = "",
        budget_action: str = "allow",
        budget_warning: str = "",
    ) -> RoutingDecision:
        info = self._registry.get(model)
        provider = info.provider if info else "unknown"
        api_base = self._config.provider_api_bases.get(provider, "")

        return RoutingDecision(
            original_model=model,
            routed_model=model,
            tier=tier or (info.tier if info else Tier.COMPLEX),
            provider=provider,
            api_base=api_base,
            confidence=confidence,
            reason=reason,
            budget_action=budget_action,
            budget_warning=budget_warning,
        )
