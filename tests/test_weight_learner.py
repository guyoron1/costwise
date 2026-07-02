"""Tests for adaptive weight learner (Phase 4)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from costwise.core.classifier import ClassifierConfig
from costwise.core.models import SignalBundle
from costwise.feedback.weight_learner import WeightLearner, _MAX_DRIFT, _SIGNAL_TO_WEIGHT
from costwise.tracking.store import TrackingStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> TrackingStore:
    store = TrackingStore(tmp_path / "test.db")
    store._get_conn()
    return store


@pytest.fixture
def classifier_config() -> ClassifierConfig:
    return ClassifierConfig()


@pytest.fixture
def learner(tmp_store: TrackingStore, classifier_config: ClassifierConfig) -> WeightLearner:
    return WeightLearner(store=tmp_store, classifier_config=classifier_config)


# ── Weight bounding ──────────────────────────────


class TestWeightBounding:
    """Verify weights never drift beyond ±30% of their defaults."""

    @pytest.mark.asyncio
    async def test_extreme_positive_correlation_bounded(self, learner: WeightLearner) -> None:
        correlations = {sig: 1000.0 for sig in _SIGNAL_TO_WEIGHT}
        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)

        await learner.maybe_adjust()

        for attr in _SIGNAL_TO_WEIGHT.values():
            default = learner._default_weights[attr]
            actual = getattr(learner.classifier_config, attr)
            assert actual <= default * (1.0 + _MAX_DRIFT) + 0.001
            assert actual >= default * (1.0 - _MAX_DRIFT) - 0.001

    @pytest.mark.asyncio
    async def test_extreme_negative_correlation_bounded(self, learner: WeightLearner) -> None:
        correlations = {sig: -1000.0 for sig in _SIGNAL_TO_WEIGHT}
        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)

        await learner.maybe_adjust()

        for attr in _SIGNAL_TO_WEIGHT.values():
            default = learner._default_weights[attr]
            actual = getattr(learner.classifier_config, attr)
            assert actual >= default * (1.0 - _MAX_DRIFT) - 0.001

    @pytest.mark.asyncio
    async def test_zero_correlation_no_change(self, learner: WeightLearner) -> None:
        correlations = {sig: 0.0 for sig in _SIGNAL_TO_WEIGHT}
        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)

        original_weights = {
            attr: getattr(learner.classifier_config, attr)
            for attr in _SIGNAL_TO_WEIGHT.values()
        }

        await learner.maybe_adjust()

        for attr, original in original_weights.items():
            assert getattr(learner.classifier_config, attr) == original


# ── Correlation-to-weight mapping ────────────────


class TestCorrelationMapping:
    """Verify correct direction and magnitude of weight adjustments."""

    @pytest.mark.asyncio
    async def test_positive_correlation_increases_weight(self, learner: WeightLearner) -> None:
        correlations = {sig: 0.0 for sig in _SIGNAL_TO_WEIGHT}
        correlations["error_severity"] = 0.5

        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)
        original = learner._default_weights["w_error"]

        await learner.maybe_adjust()

        assert getattr(learner.classifier_config, "w_error") > original

    @pytest.mark.asyncio
    async def test_negative_correlation_decreases_weight(self, learner: WeightLearner) -> None:
        correlations = {sig: 0.0 for sig in _SIGNAL_TO_WEIGHT}
        correlations["has_tools"] = -0.5

        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)
        original = learner._default_weights["w_tools"]

        await learner.maybe_adjust()

        assert getattr(learner.classifier_config, "w_tools") < original

    @pytest.mark.asyncio
    async def test_relative_magnitude_preserved(self, learner: WeightLearner) -> None:
        correlations = {sig: 0.0 for sig in _SIGNAL_TO_WEIGHT}
        correlations["error_severity"] = 1.0
        correlations["has_tools"] = 0.5

        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)

        await learner.maybe_adjust()

        error_delta = (
            getattr(learner.classifier_config, "w_error")
            - learner._default_weights["w_error"]
        )
        tools_delta = (
            getattr(learner.classifier_config, "w_tools")
            - learner._default_weights["w_tools"]
        )
        assert abs(error_delta) > abs(tools_delta)


# ── Rate limiting ────────────────────────────────


class TestRateLimiting:
    """Verify the 1-hour interval guard."""

    @pytest.mark.asyncio
    async def test_second_call_within_interval_is_noop(self, learner: WeightLearner) -> None:
        correlations = {sig: 0.5 for sig in _SIGNAL_TO_WEIGHT}
        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations)

        result1 = await learner.maybe_adjust()
        assert result1 is True

        result2 = await learner.maybe_adjust()
        assert result2 is False

    @pytest.mark.asyncio
    async def test_call_after_interval_succeeds(self, learner: WeightLearner) -> None:
        correlations_1 = {sig: 0.5 for sig in _SIGNAL_TO_WEIGHT}
        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations_1)

        await learner.maybe_adjust()

        learner._last_adjustment = time.monotonic() - 3601

        correlations_2 = {sig: -0.5 for sig in _SIGNAL_TO_WEIGHT}
        learner.store.get_signal_retry_correlations = AsyncMock(return_value=correlations_2)

        result = await learner.maybe_adjust()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_adjustment_when_empty_correlations(self, learner: WeightLearner) -> None:
        learner.store.get_signal_retry_correlations = AsyncMock(return_value={})
        result = await learner.maybe_adjust()
        assert result is False


# ── Signal snapshot storage ──────────────────────


class TestSignalSnapshotStorage:
    """Verify signal snapshots are correctly stored and retrieved."""

    @pytest.mark.asyncio
    async def test_record_and_correlate(self, tmp_store: TrackingStore) -> None:
        from costwise.tracking.store import RoutingRecord

        record = RoutingRecord(
            endpoint="/v1/messages",
            request_model="claude-opus-4-7",
            session_id="test-session",
            tier="SIMPLE",
        )
        request_id = await tmp_store.record_request(record)

        signals = SignalBundle(
            token_count=500,
            has_tools=True,
            tool_count=2,
            has_code=True,
            code_block_count=3,
            conversation_depth=5,
            has_error_context=True,
            error_severity=0.6,
            has_retry_context=False,
            image_count=0,
            intent="fix",
            multi_file_scope=True,
            referenced_file_count=3,
            graph_complexity=0.4,
        )
        await tmp_store.record_signal_snapshot(request_id, signals)

        correlations = await tmp_store.get_signal_retry_correlations(window_hours=1)
        assert isinstance(correlations, dict)
        assert "token_count" in correlations
        assert "error_severity" in correlations


# ── End-to-end simulation ────────────────────────


class TestEndToEnd:
    """Simulate a sequence of requests and retries, verify weight drift direction."""

    @pytest.mark.asyncio
    async def test_retry_heavy_signal_gets_upweighted(
        self, tmp_store: TrackingStore, classifier_config: ClassifierConfig,
    ) -> None:
        from costwise.tracking.store import RoutingRecord

        original_error_weight = classifier_config.w_error

        for i in range(20):
            record = RoutingRecord(
                endpoint="/v1/messages",
                request_model="claude-haiku-4-5",
                session_id="e2e-session",
                tier="SIMPLE",
                routed_model="claude-haiku-4-5",
            )
            request_id = await tmp_store.record_request(record)

            has_error = i < 10
            signals = SignalBundle(
                token_count=300,
                has_error_context=has_error,
                error_severity=0.6 if has_error else 0.0,
                intent="fix" if has_error else "chat",
            )
            await tmp_store.record_signal_snapshot(request_id, signals)

            if has_error:
                await tmp_store.record_retry_event(
                    session_id="e2e-session",
                    original_request_id=request_id,
                    retry_request_id=request_id + 100,
                    content_hash=f"hash-{i}",
                    similarity_score=0.85,
                    original_tier="SIMPLE",
                    original_model="claude-haiku-4-5",
                    time_delta_s=30.0,
                    was_downgraded=True,
                )

        learner = WeightLearner(store=tmp_store, classifier_config=classifier_config)
        adjusted = await learner.maybe_adjust()

        assert adjusted is True
        assert classifier_config.w_error > original_error_weight
