"""Tests for DashboardDataCollector and new TrackingStore query methods."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from costwise.config.schema import CostwiseConfig, IntegrationsConfig, TrackingConfig
from costwise.dashboard.data import DashboardData, DashboardDataCollector
from costwise.tracking.store import TrackingStore


def _insert_test_data(db_path: Path, count: int = 10) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            session_id TEXT, request_model TEXT, routed_model TEXT, tier TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
            cost_usd REAL, saved_usd REAL, latency_ms REAL,
            classification TEXT, provider TEXT, endpoint TEXT,
            status_code INTEGER, error TEXT,
            tokens_pruned INTEGER, messages_pruned INTEGER
        );
        CREATE TABLE IF NOT EXISTS provider_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            provider TEXT, model TEXT, latency_ms REAL,
            status_code INTEGER, rate_limited INTEGER DEFAULT 0, error TEXT
        );
        CREATE TABLE IF NOT EXISTS budget_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            alert_type TEXT, threshold_usd REAL, current_usd REAL, action_taken TEXT
        );
        """
    )
    for i in range(count):
        tier = ["SIMPLE", "MEDIUM", "COMPLEX"][i % 3]
        model = ["claude-haiku-3.5", "claude-sonnet-4", "claude-opus-4"][i % 3]
        conn.execute(
            """INSERT INTO routing_decisions
               (session_id, request_model, routed_model, tier,
                prompt_tokens, completion_tokens, total_tokens,
                cost_usd, saved_usd, latency_ms, provider, endpoint, status_code,
                tokens_pruned, messages_pruned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "test-session", "claude-opus-4", model, tier,
                1000 * (i + 1), 200 * (i + 1), 1200 * (i + 1),
                0.05 * (i + 1), 0.02 * (i + 1), 100 + i * 50,
                "anthropic", "/v1/messages", 200,
                50 * i, i,
            ),
        )
    conn.execute(
        "INSERT INTO budget_alerts (alert_type, threshold_usd, current_usd, action_taken) "
        "VALUES ('hourly', 10.0, 8.5, 'warn')"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def test_store(tmp_path: Path) -> TrackingStore:
    db_path = tmp_path / "test.db"
    _insert_test_data(db_path)
    return TrackingStore(db_path)


@pytest.fixture
def test_config(tmp_path: Path) -> CostwiseConfig:
    return CostwiseConfig(
        tracking=TrackingConfig(db_path=tmp_path / "test.db"),
        integrations=IntegrationsConfig(
            rtk_enabled=False,
            ponytail_enabled=False,
            headroom_enabled=False,
        ),
    )


@pytest.fixture
def empty_store(tmp_path: Path) -> TrackingStore:
    return TrackingStore(tmp_path / "empty.db")


class TestStoreNewQueries:
    async def test_model_distribution(self, test_store: TrackingStore) -> None:
        await test_store.initialize()
        dist = await test_store.get_model_distribution()
        assert len(dist) > 0
        assert all("routed_model" in d and "count" in d for d in dist)
        total_count = sum(d["count"] for d in dist)
        assert total_count == 10

    async def test_model_distribution_with_since(self, test_store: TrackingStore) -> None:
        await test_store.initialize()
        dist = await test_store.get_model_distribution(since="2020-01-01")
        assert len(dist) > 0

    async def test_tier_distribution(self, test_store: TrackingStore) -> None:
        await test_store.initialize()
        dist = await test_store.get_tier_distribution()
        assert len(dist) > 0
        tiers = {d["tier"] for d in dist}
        assert tiers == {"SIMPLE", "MEDIUM", "COMPLEX"}

    async def test_hourly_cost_series(self, test_store: TrackingStore) -> None:
        await test_store.initialize()
        series = await test_store.get_hourly_cost_series(24)
        assert isinstance(series, list)
        if series:
            assert "hour" in series[0]
            assert "cost" in series[0]
            assert "requests" in series[0]

    async def test_savings_breakdown(self, test_store: TrackingStore) -> None:
        await test_store.initialize()
        breakdown = await test_store.get_savings_breakdown()
        assert "routing_saved_usd" in breakdown
        assert "total_tokens_pruned" in breakdown
        assert "total_messages_pruned" in breakdown
        assert breakdown["routing_saved_usd"] > 0
        assert breakdown["total_tokens_pruned"] > 0

    async def test_budget_alerts(self, test_store: TrackingStore) -> None:
        await test_store.initialize()
        alerts = await test_store.get_budget_alerts(10)
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "hourly"
        assert alerts[0]["action_taken"] == "warn"

    async def test_empty_model_distribution(self, empty_store: TrackingStore) -> None:
        await empty_store.initialize()
        dist = await empty_store.get_model_distribution()
        assert dist == []

    async def test_empty_savings_breakdown(self, empty_store: TrackingStore) -> None:
        await empty_store.initialize()
        breakdown = await empty_store.get_savings_breakdown()
        assert breakdown["routing_saved_usd"] == 0.0
        assert breakdown["total_requests"] == 0


class TestDashboardDataCollector:
    async def test_collect_with_data(
        self, test_store: TrackingStore, test_config: CostwiseConfig,
    ) -> None:
        collector = DashboardDataCollector(test_store, test_config)
        data = await collector.collect()
        assert isinstance(data, DashboardData)
        assert data.gain_summary.get("total_requests") == 10
        assert len(data.recent_requests) > 0
        assert len(data.model_distribution) > 0

    async def test_collect_with_empty_db(
        self, empty_store: TrackingStore, test_config: CostwiseConfig,
    ) -> None:
        collector = DashboardDataCollector(empty_store, test_config)
        data = await collector.collect()
        assert isinstance(data, DashboardData)
        assert data.gain_summary.get("total_requests", 0) == 0
        assert data.recent_requests == []

    async def test_integrations_disabled(
        self, test_store: TrackingStore, test_config: CostwiseConfig,
    ) -> None:
        collector = DashboardDataCollector(test_store, test_config)
        data = await collector.collect()
        assert data.rtk_summary is None
        assert data.ponytail_config is None
        assert data.headroom_available is False

    async def test_data_is_frozen(
        self, test_store: TrackingStore, test_config: CostwiseConfig,
    ) -> None:
        collector = DashboardDataCollector(test_store, test_config)
        data = await collector.collect()
        with pytest.raises(AttributeError):
            data.hourly_spend = 999  # type: ignore[misc]

    async def test_savings_breakdown_populated(
        self, test_store: TrackingStore, test_config: CostwiseConfig,
    ) -> None:
        collector = DashboardDataCollector(test_store, test_config)
        data = await collector.collect()
        assert "routing_saved_usd" in data.savings_breakdown

    async def test_budget_alerts_populated(
        self, test_store: TrackingStore, test_config: CostwiseConfig,
    ) -> None:
        collector = DashboardDataCollector(test_store, test_config)
        data = await collector.collect()
        assert len(data.budget_alerts) == 1
