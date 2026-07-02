"""Data models for routing decisions, cost estimates, and complexity tiers."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Tier(str, Enum):
    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"


class CostEstimate(BaseModel):
    model: str
    provider: str
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0

    @property
    def estimated_input_cost(self) -> float:
        return self.estimated_input_tokens * self.input_cost_per_mtok / 1_000_000

    @property
    def estimated_output_cost(self) -> float:
        return self.estimated_output_tokens * self.output_cost_per_mtok / 1_000_000

    @property
    def estimated_total_cost(self) -> float:
        return self.estimated_input_cost + self.estimated_output_cost


class ModelInfo(BaseModel):
    """A model in the pricing registry."""

    name: str
    provider: str
    tier: Tier
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    context_window: int = 200_000
    max_output_tokens: int = 8_192
    supports_tools: bool = True
    supports_vision: bool = False

    @property
    def blended_cost_per_mtok(self) -> float:
        """Rough blended cost assuming 3:1 input:output ratio."""
        return (self.input_cost_per_mtok * 3 + self.output_cost_per_mtok) / 4


class RoutingDecision(BaseModel):
    """The router's output: which model to use and why."""

    original_model: str
    routed_model: str
    tier: Tier
    provider: str
    api_base: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    cost_estimate: CostEstimate | None = None
    baseline_cost: CostEstimate | None = None
    budget_action: str = "allow"
    budget_warning: str = ""
    fallback_chain: list[str] = Field(default_factory=list)

    @property
    def is_rerouted(self) -> bool:
        return self.original_model != self.routed_model

    @property
    def estimated_savings_usd(self) -> float:
        if self.baseline_cost and self.cost_estimate:
            return max(0.0, self.baseline_cost.estimated_total_cost - self.cost_estimate.estimated_total_cost)
        return 0.0


class SignalBundle(BaseModel):
    """Extracted signals from a request, used by the classifier."""

    token_count: int = 0
    has_tools: bool = False
    tool_count: int = 0
    has_code: bool = False
    code_block_count: int = 0
    conversation_depth: int = 0
    has_error_context: bool = False
    has_retry_context: bool = False
    has_system_prompt: bool = False
    system_prompt_length: int = 0
    image_count: int = 0
    ponytail_mode: str | None = None
    graph_complexity: float = 0.0

    # Semantic signals (Phase 2)
    intent: str = "unknown"
    error_severity: float = 0.0
    multi_file_scope: bool = False
    referenced_file_count: int = 0
