"""Tests for feedback threshold tuner."""

from __future__ import annotations

from pathlib import Path

import pytest

from costwise.config.schema import FeedbackConfig
from costwise.core.classifier import ClassifierConfig
from costwise.feedback.detector import RetryEvent
from costwise.feedback.tuner import ThresholdTuner
from costwise.tracking.store import TrackingStore


@pytest.fixture
async def store(tmp_path: Path) -> TrackingStore:
    s = TrackingStore(tmp_path / "test.db")
    await s.initialize()
    return s


def _event(tier: str = "SIMPLE", similarity: float = 0.9) -> RetryEvent:
    return RetryEvent(
        session_id="sess1",
        original_request_id=1,
        content_hash="abc123",
        similarity_score=similarity,
        original_tier=tier,
        original_model="claude-opus-4-7",
        time_delta_s=30.0,
        was_downgraded=True,
    )


def _tuner(
    store: TrackingStore,
    simple: float = 0.20,
    complex: float = 0.55,
    min_requests: int = 0,
) -> tuple[ThresholdTuner, ClassifierConfig]:
    cc = ClassifierConfig(simple_threshold=simple, complex_threshold=complex)
    fc = FeedbackConfig(min_requests_for_tuning=min_requests)
    t = ThresholdTuner(cc, fc, store)
    return t, cc


class TestTunerNudge:
    async def test_simple_retry_lowers_threshold(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store)
        nudged = await tuner.on_retry(_event(tier="SIMPLE"))
        assert nudged is True
        assert cc.simple_threshold == pytest.approx(0.19)

    async def test_medium_retry_lowers_complex_threshold(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store)
        nudged = await tuner.on_retry(_event(tier="MEDIUM"))
        assert nudged is True
        assert cc.complex_threshold == pytest.approx(0.54)

    async def test_complex_retry_no_nudge(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store)
        nudged = await tuner.on_retry(_event(tier="COMPLEX"))
        assert nudged is False
        assert cc.simple_threshold == pytest.approx(0.20)
        assert cc.complex_threshold == pytest.approx(0.55)


class TestTunerBounds:
    async def test_simple_respects_min(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store, simple=0.05)
        nudged = await tuner.on_retry(_event(tier="SIMPLE"))
        assert nudged is False
        assert cc.simple_threshold == pytest.approx(0.05)

    async def test_complex_respects_min(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store, complex=0.35)
        nudged = await tuner.on_retry(_event(tier="MEDIUM"))
        assert nudged is False
        assert cc.complex_threshold == pytest.approx(0.35)

    async def test_gap_preserved(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store, simple=0.20, complex=0.35)
        nudged = await tuner.on_retry(_event(tier="MEDIUM"))
        assert nudged is False
        assert cc.complex_threshold == pytest.approx(0.35)
        assert cc.simple_threshold == pytest.approx(0.20)


class TestTunerRateLimiting:
    async def test_max_nudges_per_hour(self, store: TrackingStore) -> None:
        cc = ClassifierConfig(simple_threshold=0.30, complex_threshold=0.55)
        fc = FeedbackConfig(min_requests_for_tuning=0, max_nudges_per_hour=2)
        tuner = ThresholdTuner(cc, fc, store)

        assert await tuner.on_retry(_event()) is True
        assert await tuner.on_retry(_event()) is True
        assert await tuner.on_retry(_event()) is False

    async def test_min_requests_required(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store, min_requests=100)
        nudged = await tuner.on_retry(_event())
        assert nudged is False
        assert cc.simple_threshold == pytest.approx(0.20)


class TestTunerConfig:
    async def test_auto_tune_disabled(self, store: TrackingStore) -> None:
        cc = ClassifierConfig()
        fc = FeedbackConfig(auto_tune=False)
        tuner = ThresholdTuner(cc, fc, store)
        nudged = await tuner.on_retry(_event())
        assert nudged is False

    async def test_live_config_updated(self, store: TrackingStore) -> None:
        tuner, cc = _tuner(store)
        original = cc.simple_threshold
        await tuner.on_retry(_event(tier="SIMPLE"))
        assert cc.simple_threshold < original

    async def test_nudge_count_tracked(self, store: TrackingStore) -> None:
        tuner, _ = _tuner(store, simple=0.30)
        assert tuner.nudge_count_this_hour == 0
        await tuner.on_retry(_event())
        assert tuner.nudge_count_this_hour == 1

    async def test_request_count(self, store: TrackingStore) -> None:
        tuner, _ = _tuner(store)
        assert tuner._request_count == 0
        tuner.record_request()
        assert tuner._request_count == 1

    async def test_adjustment_recorded_in_store(self, store: TrackingStore) -> None:
        tuner, _ = _tuner(store)
        await tuner.on_retry(_event())
        history = await store.get_threshold_history(limit=5)
        assert len(history) == 1
        assert history[0]["field"] == "simple_threshold"
        assert "retry on SIMPLE" in history[0]["reason"]
