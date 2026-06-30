"""Tests for feedback retry detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from costwise.feedback.detector import RetryDetector, RetryEvent
from costwise.feedback.fingerprint import fingerprint
from costwise.tracking.store import RoutingRecord, TrackingStore


@pytest.fixture
async def store(tmp_path: Path) -> TrackingStore:
    s = TrackingStore(tmp_path / "test.db")
    await s.initialize()
    return s


def _record(
    session_id: str = "sess1",
    request_model: str = "claude-opus-4-7",
    routed_model: str | None = None,
    tier: str = "SIMPLE",
    content_hash: str | None = None,
    prompt_tokens: int = 500,
    status_code: int = 200,
) -> RoutingRecord:
    return RoutingRecord(
        endpoint="/v1/messages",
        session_id=session_id,
        request_model=request_model,
        routed_model=routed_model,
        tier=tier,
        content_hash=content_hash,
        prompt_tokens=prompt_tokens,
        status_code=status_code,
    )


class TestRetryDetector:
    async def test_no_history_returns_none(self, store: TrackingStore) -> None:
        detector = RetryDetector(store)
        msgs = [{"role": "user", "content": "fix the bug"}]
        result = await detector.check("sess1", msgs)
        assert result is None

    async def test_identical_retry_detected(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "fix the authentication bug"}]
        h = fingerprint(msgs)
        await store.record_request(_record(
            content_hash=h, routed_model="claude-haiku-4-5", tier="SIMPLE",
        ))
        detector = RetryDetector(store)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is not None
        assert isinstance(result, RetryEvent)
        assert result.similarity_score == 1.0
        assert result.was_downgraded is True

    async def test_different_session_not_detected(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "fix the bug"}]
        h = fingerprint(msgs)
        await store.record_request(_record(
            session_id="other_session", content_hash=h, routed_model="claude-haiku-4-5",
        ))
        detector = RetryDetector(store)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is None

    async def test_non_downgraded_not_flagged_as_downgrade(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "fix the bug"}]
        h = fingerprint(msgs)
        await store.record_request(_record(content_hash=h, routed_model=None))
        detector = RetryDetector(store)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is not None
        assert result.was_downgraded is False

    async def test_dissimilar_content_not_detected(self, store: TrackingStore) -> None:
        original_msgs = [{"role": "user", "content": "deploy the kubernetes cluster"}]
        retry_msgs = [{"role": "user", "content": "fix the authentication error in login flow"}]
        h_orig = fingerprint(original_msgs)
        await store.record_request(_record(
            content_hash=h_orig, routed_model="claude-haiku-4-5",
        ))
        detector = RetryDetector(store, similarity_threshold=0.7)
        result = await detector.check("sess1", retry_msgs)
        assert result is None

    async def test_returns_correct_original_id(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "explain the codebase"}]
        h = fingerprint(msgs)
        row_id = await store.record_request(_record(
            content_hash=h, routed_model="claude-haiku-4-5",
        ))
        detector = RetryDetector(store)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is not None
        assert result.original_request_id == row_id

    async def test_no_content_hash_skipped(self, store: TrackingStore) -> None:
        await store.record_request(_record(content_hash=None))
        msgs = [{"role": "user", "content": "fix the bug"}]
        detector = RetryDetector(store)
        result = await detector.check("sess1", msgs)
        assert result is None

    async def test_custom_window(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "fix something"}]
        h = fingerprint(msgs)
        await store.record_request(_record(content_hash=h, routed_model="haiku"))
        detector = RetryDetector(store, window_minutes=1)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is not None

    async def test_custom_threshold(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "fix the bug"}]
        h = fingerprint(msgs)
        await store.record_request(_record(content_hash=h, routed_model="haiku"))
        detector = RetryDetector(store, similarity_threshold=1.1)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is None

    async def test_original_tier_recorded(self, store: TrackingStore) -> None:
        msgs = [{"role": "user", "content": "refactor the module"}]
        h = fingerprint(msgs)
        await store.record_request(_record(
            content_hash=h, routed_model="claude-haiku-4-5", tier="MEDIUM",
        ))
        detector = RetryDetector(store)
        result = await detector.check("sess1", msgs, content_hash=h)
        assert result is not None
        assert result.original_tier == "MEDIUM"
