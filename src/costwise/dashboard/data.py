"""Dashboard data aggregation from all Costwise sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from costwise.config.schema import CostwiseConfig
from costwise.tracking.store import TrackingStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardData:
    gain_summary: dict[str, Any] = field(default_factory=dict)
    recent_requests: list[dict[str, Any]] = field(default_factory=list)
    model_distribution: list[dict[str, Any]] = field(default_factory=list)
    tier_distribution: list[dict[str, Any]] = field(default_factory=list)
    hourly_costs: list[dict[str, Any]] = field(default_factory=list)
    savings_breakdown: dict[str, Any] = field(default_factory=dict)
    budget_alerts: list[dict[str, Any]] = field(default_factory=list)
    hourly_spend: float = 0.0
    rtk_summary: Any = None
    rtk_daily: list[Any] = field(default_factory=list)
    ponytail_config: Any = None
    headroom_available: bool = False
    provider_health: dict[str, Any] = field(default_factory=dict)
    feedback_summary: dict[str, Any] = field(default_factory=dict)


class DashboardDataCollector:
    def __init__(self, store: TrackingStore, config: CostwiseConfig) -> None:
        self._store = store
        self._config = config

    async def collect(self) -> DashboardData:
        gain_summary = await self._safe(self._store.get_gain_summary(), {})
        recent_requests = await self._safe(self._store.get_recent_requests(20), [])
        model_distribution = await self._safe(self._store.get_model_distribution(), [])
        tier_distribution = await self._safe(self._store.get_tier_distribution(), [])
        hourly_costs = await self._safe(self._store.get_hourly_cost_series(24), [])
        savings_breakdown = await self._safe(self._store.get_savings_breakdown(), {})
        budget_alerts = await self._safe(self._store.get_budget_alerts(10), [])
        hourly_spend = await self._safe(self._store.get_hourly_spend(), 0.0)

        rtk_summary = self._collect_rtk_summary()
        rtk_daily = self._collect_rtk_daily()
        ponytail_config = self._collect_ponytail()
        headroom_available = self._check_headroom()
        provider_health = self._collect_provider_health()

        feedback_summary = await self._safe(self._store.get_feedback_summary(), {})

        return DashboardData(
            gain_summary=gain_summary,
            recent_requests=recent_requests,
            model_distribution=model_distribution,
            tier_distribution=tier_distribution,
            hourly_costs=hourly_costs,
            savings_breakdown=savings_breakdown,
            budget_alerts=budget_alerts,
            hourly_spend=hourly_spend,
            rtk_summary=rtk_summary,
            rtk_daily=rtk_daily,
            ponytail_config=ponytail_config,
            headroom_available=headroom_available,
            provider_health=provider_health,
            feedback_summary=feedback_summary,
        )

    @staticmethod
    async def _safe(coro: Any, default: Any) -> Any:
        try:
            return await coro
        except Exception:
            logger.debug("Dashboard data source failed", exc_info=True)
            return default

    def _collect_rtk_summary(self) -> Any:
        if not self._config.integrations.rtk_enabled:
            return None
        try:
            from costwise.integrations.rtk import RtkReader

            reader = RtkReader(self._config.integrations.rtk_db_path)
            if not reader.available:
                return None
            summary = reader.get_summary()
            reader.close()
            return summary
        except Exception:
            logger.debug("RTK data unavailable", exc_info=True)
            return None

    def _collect_rtk_daily(self) -> list[Any]:
        if not self._config.integrations.rtk_enabled:
            return []
        try:
            from costwise.integrations.rtk import RtkReader

            reader = RtkReader(self._config.integrations.rtk_db_path)
            if not reader.available:
                return []
            daily = reader.get_daily_savings(30)
            reader.close()
            return daily
        except Exception:
            logger.debug("RTK daily data unavailable", exc_info=True)
            return []

    def _collect_ponytail(self) -> Any:
        if not self._config.integrations.ponytail_enabled:
            return None
        try:
            from costwise.integrations.ponytail import PonytailReader

            reader = PonytailReader(self._config.integrations.ponytail_config_path)
            return reader.get_config()
        except Exception:
            logger.debug("Ponytail data unavailable", exc_info=True)
            return None

    def _check_headroom(self) -> bool:
        if not self._config.integrations.headroom_enabled:
            return False
        try:
            from costwise.integrations.headroom import is_available

            return is_available()
        except Exception:
            return False

    def _collect_provider_health(self) -> dict[str, Any]:
        try:
            from costwise.core.health import ProviderHealthTracker

            tracker = ProviderHealthTracker()
            return {k: v for k, v in tracker.get_all_snapshots().items()}
        except Exception:
            logger.debug("Provider health unavailable", exc_info=True)
            return {}
