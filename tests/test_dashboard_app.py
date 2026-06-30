"""Tests for the dashboard FastAPI app endpoints."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from costwise.config.schema import CostwiseConfig, IntegrationsConfig, TrackingConfig
from costwise.dashboard.app import create_dashboard_app
from costwise.tracking.store import TrackingStore


def _seed_db(db_path: Path) -> None:
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
        INSERT INTO routing_decisions
            (session_id, request_model, routed_model, tier,
             prompt_tokens, completion_tokens, total_tokens,
             cost_usd, saved_usd, latency_ms, provider, endpoint, status_code,
             tokens_pruned, messages_pruned)
        VALUES
            ('s1', 'claude-opus-4', 'claude-sonnet-4', 'MEDIUM',
             5000, 1000, 6000, 0.10, 0.05, 250, 'anthropic', '/v1/messages', 200,
             100, 2);
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def dashboard_app(tmp_path: Path):
    db_path = tmp_path / "dash.db"
    _seed_db(db_path)
    config = CostwiseConfig(
        tracking=TrackingConfig(db_path=db_path),
        integrations=IntegrationsConfig(
            rtk_enabled=False,
            ponytail_enabled=False,
            headroom_enabled=False,
        ),
    )
    store = TrackingStore(db_path)
    return create_dashboard_app(config, store)


@pytest.fixture
async def client(dashboard_app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=dashboard_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDashboardEndpoints:
    async def test_health(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_main_page(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Costwise" in resp.text

    async def test_api_summary(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "gain_summary" in data
        assert "model_distribution" in data
        assert "hourly_costs" in data

    async def test_api_requests(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/requests")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["routed_model"] == "claude-sonnet-4"

    async def test_api_costs(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/costs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_api_models(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["routed_model"] == "claude-sonnet-4"

    async def test_api_health(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    async def test_api_budget(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "hourly_spend" in data
        assert "alerts" in data


class TestDashboardPartials:
    async def test_partial_requests(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/partials/requests")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_partial_costs(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/partials/costs")
        assert resp.status_code == 200
        assert "<svg" in resp.text

    async def test_partial_models(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/partials/models")
        assert resp.status_code == 200
        assert "<svg" in resp.text
