"""Tests for Costwise MCP server tool functions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import costwise.mcp.server as mcp_mod
from costwise.config.schema import CostwiseConfig, IntegrationsConfig, TrackingConfig


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
        """
    )
    for i in range(5):
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
                1000, 200, 1200, 0.05, 0.02, 150,
                "anthropic", "/v1/messages", 200, 50, 1,
            ),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_mcp_globals():
    """Reset module-level singletons between tests."""
    mcp_mod._config = None
    mcp_mod._store = None
    mcp_mod._router = None
    mcp_mod._registry = None
    yield
    mcp_mod._config = None
    mcp_mod._store = None
    mcp_mod._router = None
    mcp_mod._registry = None


@pytest.fixture
def mcp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "mcp_test.db"
    _seed_db(db_path)
    return db_path


@pytest.fixture
def mock_config(mcp_db: Path) -> CostwiseConfig:
    return CostwiseConfig(
        tracking=TrackingConfig(db_path=mcp_db),
        integrations=IntegrationsConfig(
            rtk_enabled=False,
            ponytail_enabled=False,
            headroom_enabled=False,
        ),
    )


class TestCostwiseRoute:
    async def test_simple_prompt(self, mock_config: CostwiseConfig) -> None:
        with patch.object(mcp_mod, "_config", mock_config):
            mcp_mod._config = mock_config
            result_str = await mcp_mod.costwise_route("What time is it?")
            result = json.loads(result_str)
            assert "recommended_model" in result
            assert "tier" in result
            assert result["tier"] in ["SIMPLE", "MEDIUM", "COMPLEX"]
            assert "confidence" in result

    async def test_complex_prompt(self, mock_config: CostwiseConfig) -> None:
        with patch.object(mcp_mod, "_config", mock_config):
            mcp_mod._config = mock_config
            complex_prompt = (
                "Refactor the authentication middleware to use JWT tokens. "
                "Error: the current implementation has a race condition. "
                "Retry: previous attempt failed with a segfault. "
                "```python\ndef auth():\n    pass\n```"
            )
            result_str = await mcp_mod.costwise_route(complex_prompt, model="claude-opus-4")
            result = json.loads(result_str)
            assert "recommended_model" in result
            assert "tier" in result

    async def test_returns_valid_json(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_route("Hello world")
        result = json.loads(result_str)
        assert isinstance(result, dict)
        assert "original_model" in result
        assert "provider" in result


class TestCostwiseBudget:
    async def test_no_graph(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_budget(["src/main.py", "src/utils.py"])
        result = json.loads(result_str)
        assert result["status"] == "no_graph"

    async def test_with_files(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_budget(["a.py", "b.py"], token_budget=5000)
        result = json.loads(result_str)
        assert "status" in result


class TestCostwiseStats:
    async def test_all_sessions(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_stats()
        result = json.loads(result_str)
        assert "total_cost_usd" in result
        assert "total_saved_usd" in result
        assert "total_requests" in result
        assert result["total_requests"] == 5

    async def test_session_filter(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_stats(session_id="test-session")
        result = json.loads(result_str)
        assert result["session_id"] == "test-session"
        assert result["total_requests"] == 5

    async def test_nonexistent_session(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_stats(session_id="nonexistent")
        result = json.loads(result_str)
        assert result["total_requests"] == 0

    async def test_model_distribution_included(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_stats()
        result = json.loads(result_str)
        assert "model_distribution" in result
        assert isinstance(result["model_distribution"], list)


class TestCostwiseGain:
    async def test_with_data(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_gain()
        result = json.loads(result_str)
        assert "total_requests" in result
        assert result["total_requests"] == 5
        assert "layers" in result
        assert "routing" in result["layers"]
        assert "pruning" in result["layers"]

    async def test_layers_structure(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_gain()
        result = json.loads(result_str)
        assert result["layers"]["routing"]["saved_usd"] > 0
        assert result["layers"]["pruning"]["tokens_pruned"] > 0

    async def test_period_included(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_gain()
        result = json.loads(result_str)
        assert "period" in result
        assert "first_request" in result["period"]

    async def test_integrations_disabled(self, mock_config: CostwiseConfig) -> None:
        mcp_mod._config = mock_config
        result_str = await mcp_mod.costwise_gain()
        result = json.loads(result_str)
        assert "rtk" not in result["layers"]
        assert "ponytail" not in result["layers"]
