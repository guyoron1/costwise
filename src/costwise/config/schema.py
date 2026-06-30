"""Pydantic v2 models for Costwise configuration."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def _default_db_path() -> Path:
    return _xdg_data_home() / "costwise" / "costwise.db"


class ProviderConfig(BaseModel):
    name: str
    api_base: str
    api_key_env: str = ""
    enabled: bool = True


class ProxyConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8788
    upstream: str = "https://api.anthropic.com"
    timeout_s: float = 120.0


class RoutingConfig(BaseModel):
    enabled: bool = True
    simple_threshold: float = 0.20
    complex_threshold: float = 0.55
    min_confidence: float = 0.1
    enabled_providers: list[str] = Field(default_factory=lambda: ["anthropic", "openai", "google"])
    default_output_ratio: float = 0.3


class BudgetConfig(BaseModel):
    max_hourly_usd: float | None = None
    max_session_usd: float | None = None
    auto_downgrade: bool = True
    warning_threshold_pct: float = 80.0


class TrackingConfig(BaseModel):
    db_path: Path = Field(default_factory=_default_db_path)
    retention_days: int = 90


class GraphConfig(BaseModel):
    enabled: bool = True
    graph_path: str = "graphify-out/graph.json"
    relevance_threshold: float = 0.1
    max_hops: int = 4
    decay: float = 0.5
    community_boost: float = 0.2
    protect_last_n: int = 2


class FeedbackConfig(BaseModel):
    enabled: bool = True
    retry_window_minutes: int = 5
    similarity_threshold: float = 0.7
    auto_tune: bool = True
    nudge_step: float = 0.01
    simple_threshold_min: float = 0.05
    simple_threshold_max: float = 0.40
    complex_threshold_min: float = 0.35
    complex_threshold_max: float = 0.80
    min_threshold_gap: float = 0.15
    min_requests_for_tuning: int = 20
    max_nudges_per_hour: int = 5
    target_false_downgrade_rate: float = 0.03


class IntegrationsConfig(BaseModel):
    graphify_mcp: bool = False
    graphify_graph_path: str = ""
    headroom_enabled: bool = True
    headroom_proxy_chain: bool = False
    headroom_proxy_url: str = "http://127.0.0.1:8787"
    rtk_enabled: bool = True
    rtk_db_path: str = ""
    ponytail_enabled: bool = True
    ponytail_config_path: str = ""


class CostwiseConfig(BaseModel):
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    providers: list[ProviderConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_db_dir(self) -> CostwiseConfig:
        self.tracking.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self
