"""Tests for LiteLLM callback adapter."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from costwise.integrations.litellm import CostwiseCallback, _compute_latency_ms


@pytest.fixture()
def mock_store():
    store = AsyncMock()
    store.record_request = AsyncMock(return_value=1)
    return store


@pytest.fixture()
def callback(mock_store):
    return CostwiseCallback(mock_store, session_id="test-session")


class TestSuccessHandler:
    async def test_records_basic_request(self, callback, mock_store):
        kwargs = {
            "model": "claude-sonnet-4-5-20250929",
            "litellm_params": {"custom_llm_provider": "anthropic"},
            "response_cost": 0.005,
        }
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
        )

        await callback.async_success_handler(kwargs, response, 1000.0, 1002.5)

        mock_store.record_request.assert_called_once()
        record = mock_store.record_request.call_args[0][0]
        assert record.request_model == "claude-sonnet-4-5-20250929"
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50
        assert record.total_tokens == 150
        assert record.cost_usd == 0.005
        assert record.provider == "anthropic"
        assert record.session_id == "test-session"
        assert record.status_code == 200

    async def test_computes_total_from_parts(self, callback, mock_store):
        kwargs = {"model": "gpt-4o", "litellm_params": {}}
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=100, total_tokens=None)
        )

        await callback.async_success_handler(kwargs, response, 0.0, 1.0)

        record = mock_store.record_request.call_args[0][0]
        assert record.total_tokens == 300

    async def test_handles_missing_usage(self, callback, mock_store):
        kwargs = {"model": "test", "litellm_params": {}}
        response = SimpleNamespace()

        await callback.async_success_handler(kwargs, response, 0.0, 0.0)

        record = mock_store.record_request.call_args[0][0]
        assert record.prompt_tokens is None

    async def test_swallows_exceptions(self, callback, mock_store):
        mock_store.record_request.side_effect = RuntimeError("db error")
        kwargs = {"model": "test", "litellm_params": {}}
        response = SimpleNamespace()

        await callback.async_success_handler(kwargs, response, 0.0, 0.0)


class TestFailureHandler:
    async def test_records_error(self, callback, mock_store):
        kwargs = {
            "model": "claude-opus-4-20250514",
            "litellm_params": {"custom_llm_provider": "anthropic"},
        }
        exception = SimpleNamespace(status_code=429)

        await callback.async_failure_handler(kwargs, exception, 1000.0, 1001.0)

        record = mock_store.record_request.call_args[0][0]
        assert record.status_code == 429
        assert record.request_model == "claude-opus-4-20250514"
        assert record.error is not None

    async def test_handles_exception_without_status(self, callback, mock_store):
        kwargs = {"model": "test", "litellm_params": {}}
        exception = ValueError("connection refused")

        await callback.async_failure_handler(kwargs, exception, 0.0, 0.0)

        record = mock_store.record_request.call_args[0][0]
        assert record.status_code == 500
        assert "connection refused" in record.error

    async def test_swallows_record_exception(self, callback, mock_store):
        mock_store.record_request.side_effect = RuntimeError("db error")
        kwargs = {"model": "test", "litellm_params": {}}

        await callback.async_failure_handler(kwargs, ValueError("x"), 0.0, 0.0)


class TestComputeLatency:
    def test_float_timestamps(self):
        assert _compute_latency_ms(1.0, 2.0) == 1000.0

    def test_datetime_timestamps(self):
        start = datetime(2025, 1, 1, 12, 0, 0)
        end = start + timedelta(seconds=2)
        assert abs(_compute_latency_ms(start, end) - 2000.0) < 1.0

    def test_invalid_types_return_zero(self):
        assert _compute_latency_ms("a", "b") == 0.0

    def test_none_returns_zero(self):
        assert _compute_latency_ms(None, None) == 0.0
