"""Retry detector: identifies when a request is a retry of a previously-downgraded request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from costwise.feedback.fingerprint import fingerprint
from costwise.tracking.store import TrackingStore


@dataclass(frozen=True)
class RetryEvent:
    session_id: str
    original_request_id: int
    content_hash: str
    similarity_score: float
    original_tier: str
    original_model: str
    time_delta_s: float
    was_downgraded: bool


class RetryDetector:
    def __init__(
        self,
        store: TrackingStore,
        window_minutes: int = 5,
        similarity_threshold: float = 0.7,
    ) -> None:
        self._store = store
        self._window_minutes = window_minutes
        self._similarity_threshold = similarity_threshold

    async def check(
        self,
        session_id: str,
        messages: list[dict],
        content_hash: str | None = None,
    ) -> RetryEvent | None:
        """Check if the current request is a retry of a recently-downgraded request.

        Returns a RetryEvent if a retry is detected, None otherwise.
        """
        if content_hash is None:
            content_hash = fingerprint(messages)

        recent = await self._store.get_recent_fingerprints(
            session_id, window_minutes=self._window_minutes,
        )

        if not recent:
            return None

        best_match: dict | None = None
        best_score = 0.0

        for record in recent:
            stored_hash = record.get("content_hash")
            if not stored_hash:
                continue

            if stored_hash == content_hash:
                score = 1.0
            else:
                stored_tokens = record.get("prompt_tokens") or 0
                approx_tokens = sum(
                    len(m.get("content", "")) // 4
                    for m in messages
                    if isinstance(m.get("content"), str)
                )
                if stored_tokens > 0 and approx_tokens > 0:
                    ratio = min(stored_tokens, approx_tokens) / max(stored_tokens, approx_tokens)
                    if ratio < 0.5:
                        continue

                score = self._jaccard_from_hash(content_hash, stored_hash)

            if score >= self._similarity_threshold and score > best_score:
                best_score = score
                best_match = record

        if best_match is None:
            return None

        was_downgraded = best_match.get("routed_model") is not None
        ts_str = best_match.get("timestamp", "")
        try:
            record_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            now = datetime.now(record_time.tzinfo)
            time_delta = (now - record_time).total_seconds()
        except (ValueError, TypeError):
            time_delta = 0.0

        return RetryEvent(
            session_id=session_id,
            original_request_id=best_match["id"],
            content_hash=content_hash,
            similarity_score=best_score,
            original_tier=best_match.get("tier", ""),
            original_model=best_match.get("request_model", ""),
            time_delta_s=time_delta,
            was_downgraded=was_downgraded,
        )

    def _jaccard_from_hash(self, hash_a: str, hash_b: str) -> float:
        """Rough similarity from hash hex chars (fast heuristic, not semantic).

        For exact semantic similarity we'd need the original text, but this
        catches near-identical content that differs only in normalization edge cases.
        """
        set_a = set(hash_a[i:i+4] for i in range(0, len(hash_a) - 3))
        set_b = set(hash_b[i:i+4] for i in range(0, len(hash_b) - 3))
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union)
