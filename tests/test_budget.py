"""Tests for budget enforcement."""

from __future__ import annotations

import pytest

from costwise.config.schema import BudgetConfig
from costwise.core.budget import BudgetAction, BudgetEnforcer
from costwise.core.models import Tier


class TestBudgetEnforcer:

    def test_no_limits_always_allows(self):
        enforcer = BudgetEnforcer(BudgetConfig())
        result = enforcer.check(Tier.COMPLEX)
        assert result.action == BudgetAction.ALLOW

    def test_under_limit_allows(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_session_usd=50.0))
        enforcer.record_spend(10.0)
        result = enforcer.check(Tier.COMPLEX)
        assert result.action == BudgetAction.ALLOW

    def test_session_limit_exceeded_downgrades(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_session_usd=10.0, auto_downgrade=True))
        enforcer.record_spend(11.0)
        result = enforcer.check(Tier.COMPLEX)
        assert result.action == BudgetAction.DOWNGRADE
        assert result.downgrade_to == Tier.MEDIUM

    def test_session_limit_exceeded_blocks_simple(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_session_usd=10.0, auto_downgrade=True))
        enforcer.record_spend(11.0)
        result = enforcer.check(Tier.SIMPLE)
        assert result.action == BudgetAction.BLOCK

    def test_session_limit_exceeded_no_downgrade_blocks(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_session_usd=10.0, auto_downgrade=False))
        enforcer.record_spend(11.0)
        result = enforcer.check(Tier.COMPLEX)
        assert result.action == BudgetAction.BLOCK

    def test_warning_threshold(self):
        enforcer = BudgetEnforcer(
            BudgetConfig(max_session_usd=100.0, warning_threshold_pct=80.0)
        )
        enforcer.record_spend(85.0)
        result = enforcer.check(Tier.COMPLEX)
        assert result.action == BudgetAction.WARN
        assert "approaching" in result.reason

    def test_hourly_limit_exceeded(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_hourly_usd=5.0, auto_downgrade=True))
        enforcer.record_spend(6.0)
        result = enforcer.check(Tier.COMPLEX)
        assert result.action == BudgetAction.DOWNGRADE

    def test_hourly_spend_tracks_correctly(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_hourly_usd=10.0))
        enforcer.record_spend(3.0)
        enforcer.record_spend(4.0)
        assert enforcer.get_hourly_spend() == pytest.approx(7.0)

    def test_session_spend_cumulates(self):
        enforcer = BudgetEnforcer(BudgetConfig())
        enforcer.record_spend(5.0)
        enforcer.record_spend(3.0)
        assert enforcer.session_spend == pytest.approx(8.0)

    def test_downgrade_chain(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_session_usd=1.0, auto_downgrade=True))
        enforcer.record_spend(2.0)
        complex_result = enforcer.check(Tier.COMPLEX)
        assert complex_result.downgrade_to == Tier.MEDIUM
        medium_result = enforcer.check(Tier.MEDIUM)
        assert medium_result.downgrade_to == Tier.SIMPLE

    def test_budget_pct_in_result(self):
        enforcer = BudgetEnforcer(BudgetConfig(max_session_usd=100.0, max_hourly_usd=50.0))
        enforcer.record_spend(25.0)
        result = enforcer.check(Tier.COMPLEX)
        assert result.session_pct == pytest.approx(25.0)
        assert result.hourly_pct == pytest.approx(50.0)
