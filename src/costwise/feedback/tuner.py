"""Threshold auto-tuner: bounded adjustment of classifier thresholds based on retry feedback."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from costwise.config.schema import FeedbackConfig
from costwise.core.classifier import ClassifierConfig
from costwise.feedback.detector import RetryEvent
from costwise.tracking.store import TrackingStore

logger = logging.getLogger(__name__)


@dataclass
class NudgeRecord:
    timestamp: float
    field: str
    old_value: float
    new_value: float


class ThresholdTuner:
    def __init__(
        self,
        classifier_config: ClassifierConfig,
        feedback_config: FeedbackConfig,
        store: TrackingStore,
    ) -> None:
        self._classifier = classifier_config
        self._feedback = feedback_config
        self._store = store
        self._lock = threading.Lock()
        self._nudge_history: list[NudgeRecord] = []
        self._request_count = 0

    @property
    def nudge_count_this_hour(self) -> int:
        cutoff = time.monotonic() - 3600
        return sum(1 for n in self._nudge_history if n.timestamp > cutoff)

    def record_request(self) -> None:
        self._request_count += 1

    async def on_retry(self, event: RetryEvent) -> bool:
        """Process a retry event and potentially adjust thresholds.

        Returns True if a threshold was nudged, False otherwise.
        """
        if not self._feedback.auto_tune:
            return False

        with self._lock:
            if self._request_count < self._feedback.min_requests_for_tuning:
                return False

            if self.nudge_count_this_hour >= self._feedback.max_nudges_per_hour:
                return False

            tier = event.original_tier.upper()

            if tier == "SIMPLE":
                return await self._nudge_simple_down(event)
            elif tier == "MEDIUM":
                return await self._nudge_complex_down(event)

        return False

    async def maybe_relax(self) -> bool:
        """If false-downgrade rate is well below target, relax thresholds slightly."""
        if not self._feedback.auto_tune:
            return False

        if self._request_count < self._feedback.min_requests_for_tuning:
            return False

        rate_data = await self._store.get_false_downgrade_rate(window_minutes=60)
        rate = rate_data.get("false_downgrade_rate", 0.0)

        if rate >= self._feedback.target_false_downgrade_rate * 0.3:
            return False

        with self._lock:
            if self.nudge_count_this_hour >= self._feedback.max_nudges_per_hour:
                return False

            relaxed = False
            half_step = self._feedback.nudge_step / 2

            old_simple = self._classifier.simple_threshold
            new_simple = min(
                old_simple + half_step,
                self._feedback.simple_threshold_max,
            )
            if (self._classifier.complex_threshold - new_simple
                    >= self._feedback.min_threshold_gap - 1e-9):
                self._classifier.simple_threshold = new_simple
                await self._record_adjustment(
                    "simple_threshold", old_simple, new_simple,
                    f"relaxation: false_downgrade_rate={rate:.4f} < target*0.3",
                )
                relaxed = True

            old_complex = self._classifier.complex_threshold
            new_complex = min(
                old_complex + half_step,
                self._feedback.complex_threshold_max,
            )
            if (new_complex - self._classifier.simple_threshold
                    >= self._feedback.min_threshold_gap - 1e-9):
                self._classifier.complex_threshold = new_complex
                await self._record_adjustment(
                    "complex_threshold", old_complex, new_complex,
                    f"relaxation: false_downgrade_rate={rate:.4f} < target*0.3",
                )
                relaxed = True

            return relaxed

    async def _nudge_simple_down(self, event: RetryEvent) -> bool:
        old = self._classifier.simple_threshold
        new = max(old - self._feedback.nudge_step, self._feedback.simple_threshold_min)
        if self._classifier.complex_threshold - new < self._feedback.min_threshold_gap - 1e-9:
            return False
        if new == old:
            return False
        self._classifier.simple_threshold = new
        self._nudge_history.append(NudgeRecord(time.monotonic(), "simple_threshold", old, new))
        await self._record_adjustment(
            "simple_threshold", old, new,
            f"retry on SIMPLE (sim={event.similarity_score:.2f})",
            retry_event_id=event.original_request_id,
        )
        logger.info("Tuner: simple_threshold %.3f → %.3f", old, new)
        return True

    async def _nudge_complex_down(self, event: RetryEvent) -> bool:
        old = self._classifier.complex_threshold
        new = max(old - self._feedback.nudge_step, self._feedback.complex_threshold_min)
        if new - self._classifier.simple_threshold < self._feedback.min_threshold_gap - 1e-9:
            return False
        if new == old:
            return False
        self._classifier.complex_threshold = new
        self._nudge_history.append(NudgeRecord(time.monotonic(), "complex_threshold", old, new))
        await self._record_adjustment(
            "complex_threshold", old, new,
            f"retry on MEDIUM (sim={event.similarity_score:.2f})",
            retry_event_id=event.original_request_id,
        )
        logger.info("Tuner: complex_threshold %.3f → %.3f", old, new)
        return True

    async def _record_adjustment(
        self,
        field: str,
        old_value: float,
        new_value: float,
        reason: str,
        retry_event_id: int | None = None,
    ) -> None:
        rate_data = await self._store.get_retry_rate(window_minutes=60)
        await self._store.record_threshold_adjustment(
            field=field,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            retry_event_id=retry_event_id,
            window_retry_rate=rate_data.get("retry_rate"),
            window_requests=rate_data.get("total_requests"),
        )
