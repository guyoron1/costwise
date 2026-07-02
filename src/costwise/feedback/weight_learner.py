"""Adaptive weight learner: adjusts classifier signal weights based on retry correlations."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from costwise.core.classifier import ClassifierConfig
from costwise.tracking.store import TrackingStore

logger = logging.getLogger(__name__)

_SIGNAL_TO_WEIGHT: dict[str, str] = {
    "has_tools": "w_tools",
    "token_count": "w_token_count",
    "has_code": "w_code",
    "conversation_depth": "w_depth",
    "error_severity": "w_error",
    "has_retry_context": "w_retry",
    "image_count": "w_images",
    "multi_file_scope": "w_multi_file",
    "graph_complexity": "w_graph_complexity",
}

_MAX_DRIFT = 0.30


@dataclass
class WeightLearner:
    """Adjusts classifier weights based on which signals correlate with retries.

    Positive correlation (signal higher on retried requests) → increase weight.
    Negative correlation → decrease weight. Bounded to ±30% of default values.
    """

    store: TrackingStore
    classifier_config: ClassifierConfig
    _default_weights: dict[str, float] = field(default_factory=dict, init=False)
    _last_adjustment: float = field(default=float("-inf"), init=False)
    _min_adjustment_interval: float = 3600.0

    def __post_init__(self) -> None:
        for attr in _SIGNAL_TO_WEIGHT.values():
            self._default_weights[attr] = getattr(self.classifier_config, attr)

    async def maybe_adjust(self) -> bool:
        """Check correlations and adjust weights if enough time has passed."""
        now = time.monotonic()
        if now - self._last_adjustment < self._min_adjustment_interval:
            return False

        correlations = await self.store.get_signal_retry_correlations(window_hours=24)

        if not correlations:
            return False

        max_corr = max(abs(v) for v in correlations.values()) or 1.0

        adjusted = False
        for signal, attr in _SIGNAL_TO_WEIGHT.items():
            if signal not in correlations:
                continue

            default_weight = self._default_weights[attr]
            corr = correlations[signal] / max_corr  # normalize to [-1.0, 1.0]

            adjustment = corr * _MAX_DRIFT * default_weight
            new_weight = default_weight + adjustment

            lower_bound = default_weight * (1.0 - _MAX_DRIFT)
            upper_bound = default_weight * (1.0 + _MAX_DRIFT)
            new_weight = max(lower_bound, min(upper_bound, new_weight))

            old_weight = getattr(self.classifier_config, attr)
            if abs(new_weight - old_weight) > 0.001:
                setattr(self.classifier_config, attr, round(new_weight, 4))
                logger.info(
                    "WeightLearner: %s %.4f -> %.4f (corr=%.3f)",
                    attr, old_weight, new_weight, corr,
                )
                adjusted = True

        if adjusted:
            self._last_adjustment = now

        return adjusted
