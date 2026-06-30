"""Feedback metrics: false-downgrade rate, retry rate, quality grade."""

from __future__ import annotations

from typing import Any

from costwise.tracking.store import TrackingStore


def quality_grade(false_downgrade_rate: float) -> str:
    """Map false-downgrade rate to a letter grade.

    A: <1%, B: <2%, C: <3% (target), D: <5%, F: >=5%
    """
    if false_downgrade_rate < 0.01:
        return "A"
    if false_downgrade_rate < 0.02:
        return "B"
    if false_downgrade_rate < 0.03:
        return "C"
    if false_downgrade_rate < 0.05:
        return "D"
    return "F"


class FeedbackMetrics:
    def __init__(self, store: TrackingStore) -> None:
        self._store = store

    async def get_summary(
        self,
        window_minutes: int = 60,
        current_simple_threshold: float | None = None,
        current_complex_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Get a complete feedback metrics summary."""
        retry_data = await self._store.get_retry_rate(window_minutes)
        fd_data = await self._store.get_false_downgrade_rate(window_minutes)
        history = await self._store.get_threshold_history(limit=10)
        summary = await self._store.get_feedback_summary()

        fd_rate = fd_data.get("false_downgrade_rate", 0.0)
        grade = quality_grade(fd_rate)

        result: dict[str, Any] = {
            "retry_rate": retry_data.get("retry_rate", 0.0),
            "retry_count": retry_data.get("retry_count", 0),
            "false_downgrade_rate": fd_rate,
            "false_downgrade_count": fd_data.get("false_downgrade_count", 0),
            "total_requests": summary.get("total_requests", 0),
            "total_downgrades": fd_data.get("total_downgrades", 0),
            "quality_grade": grade,
            "total_threshold_adjustments": summary.get("total_threshold_adjustments", 0),
            "recent_adjustments": history,
            "window_minutes": window_minutes,
        }

        if current_simple_threshold is not None:
            result["current_simple_threshold"] = current_simple_threshold
        if current_complex_threshold is not None:
            result["current_complex_threshold"] = current_complex_threshold

        return result
