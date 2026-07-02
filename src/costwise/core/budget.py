"""Budget enforcement: spend tracking, warnings, and auto-downgrade.

Checks hourly and session spend against configured limits. When limits
are approached or exceeded, returns actions: ALLOW, WARN, DOWNGRADE, BLOCK.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from costwise.config.schema import BudgetConfig
from costwise.core.models import Tier


class BudgetAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    DOWNGRADE = "downgrade"
    BLOCK = "block"


@dataclass
class BudgetCheckResult:
    action: BudgetAction
    hourly_spend_usd: float = 0.0
    session_spend_usd: float = 0.0
    hourly_limit_usd: float | None = None
    session_limit_usd: float | None = None
    hourly_pct: float = 0.0
    session_pct: float = 0.0
    downgrade_to: Tier | None = None
    reason: str = ""


_TIER_DOWNGRADE: dict[Tier, Tier] = {
    Tier.COMPLEX: Tier.MEDIUM,
    Tier.MEDIUM: Tier.SIMPLE,
}


class BudgetEnforcer:
    """Checks spend against budget limits and decides routing action."""

    def __init__(self, config: BudgetConfig) -> None:
        self._config = config
        self._session_spend: float = 0.0
        self._hourly_records: list[tuple[float, float]] = []

    @property
    def config(self) -> BudgetConfig:
        return self._config

    @property
    def session_spend(self) -> float:
        return self._session_spend

    def record_spend(self, cost_usd: float) -> None:
        """Record a completed request's cost."""
        now = time.monotonic()
        self._session_spend += cost_usd
        self._hourly_records.append((now, cost_usd))
        self._evict_hourly()

    def get_hourly_spend(self) -> float:
        self._evict_hourly()
        return sum(cost for _, cost in self._hourly_records)

    def check(self, requested_tier: Tier) -> BudgetCheckResult:
        """Check budget and return the appropriate action for the requested tier."""
        hourly = self.get_hourly_spend()
        session = self._session_spend

        hourly_limit = self._config.max_hourly_usd
        session_limit = self._config.max_session_usd
        warn_pct = self._config.warning_threshold_pct / 100.0

        hourly_pct = (hourly / hourly_limit * 100) if hourly_limit else 0.0
        session_pct = (session / session_limit * 100) if session_limit else 0.0

        base = BudgetCheckResult(
            action=BudgetAction.ALLOW,
            hourly_spend_usd=hourly,
            session_spend_usd=session,
            hourly_limit_usd=hourly_limit,
            session_limit_usd=session_limit,
            hourly_pct=hourly_pct,
            session_pct=session_pct,
        )

        exceeded_hourly = hourly_limit is not None and hourly >= hourly_limit
        exceeded_session = session_limit is not None and session >= session_limit

        if exceeded_hourly or exceeded_session:
            source = "hourly" if exceeded_hourly else "session"
            if self._config.auto_downgrade and requested_tier in _TIER_DOWNGRADE:
                base.action = BudgetAction.DOWNGRADE
                base.downgrade_to = _TIER_DOWNGRADE[requested_tier]
                base.reason = (
                    f"{source} budget exceeded, downgrading"
                    f" {requested_tier.value} → {base.downgrade_to.value}"
                )
            else:
                base.action = BudgetAction.BLOCK
                base.reason = (
                    f"{source} budget exceeded"
                    f" (${hourly:.2f}/${hourly_limit or '∞'} hourly,"
                    f" ${session:.2f}/${session_limit or '∞'} session)"
                )
            return base

        warning_hourly = hourly_limit is not None and hourly >= hourly_limit * warn_pct
        warning_session = session_limit is not None and session >= session_limit * warn_pct

        if warning_hourly or warning_session:
            base.action = BudgetAction.WARN
            base.reason = (
                f"approaching budget limit"
                f" ({hourly_pct:.0f}% hourly, {session_pct:.0f}% session)"
            )
            return base

        base.action = BudgetAction.ALLOW
        return base

    def _evict_hourly(self) -> None:
        cutoff = time.monotonic() - 3600.0
        while self._hourly_records and self._hourly_records[0][0] < cutoff:
            self._hourly_records.pop(0)
