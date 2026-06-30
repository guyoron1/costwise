"""Tests for feedback metrics and quality grade."""

from __future__ import annotations

from pathlib import Path

import pytest

from costwise.feedback.metrics import FeedbackMetrics, quality_grade
from costwise.tracking.store import TrackingStore


@pytest.fixture
async def store(tmp_path: Path) -> TrackingStore:
    s = TrackingStore(tmp_path / "test.db")
    await s.initialize()
    return s


class TestQualityGrade:
    def test_grade_A(self) -> None:
        assert quality_grade(0.005) == "A"

    def test_grade_B(self) -> None:
        assert quality_grade(0.015) == "B"

    def test_grade_C(self) -> None:
        assert quality_grade(0.025) == "C"

    def test_grade_D(self) -> None:
        assert quality_grade(0.04) == "D"

    def test_grade_F(self) -> None:
        assert quality_grade(0.10) == "F"

    def test_grade_zero(self) -> None:
        assert quality_grade(0.0) == "A"

    def test_grade_boundary_1pct(self) -> None:
        assert quality_grade(0.01) == "B"

    def test_grade_boundary_3pct(self) -> None:
        assert quality_grade(0.03) == "D"


class TestFeedbackMetrics:
    async def test_empty_db(self, store: TrackingStore) -> None:
        metrics = FeedbackMetrics(store)
        summary = await metrics.get_summary()
        assert summary["retry_count"] == 0
        assert summary["retry_rate"] == 0.0
        assert summary["false_downgrade_rate"] == 0.0
        assert summary["quality_grade"] == "A"

    async def test_summary_structure(self, store: TrackingStore) -> None:
        metrics = FeedbackMetrics(store)
        summary = await metrics.get_summary()
        expected_keys = {
            "retry_rate", "retry_count", "false_downgrade_rate",
            "false_downgrade_count", "total_requests", "total_downgrades",
            "quality_grade", "total_threshold_adjustments",
            "recent_adjustments", "window_minutes",
        }
        assert expected_keys.issubset(set(summary.keys()))

    async def test_current_thresholds_included(self, store: TrackingStore) -> None:
        metrics = FeedbackMetrics(store)
        summary = await metrics.get_summary(
            current_simple_threshold=0.18,
            current_complex_threshold=0.52,
        )
        assert summary["current_simple_threshold"] == 0.18
        assert summary["current_complex_threshold"] == 0.52

    async def test_window_parameter(self, store: TrackingStore) -> None:
        metrics = FeedbackMetrics(store)
        summary = await metrics.get_summary(window_minutes=30)
        assert summary["window_minutes"] == 30
