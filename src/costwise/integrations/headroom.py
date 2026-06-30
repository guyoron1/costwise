"""Headroom integration — graph-aware compression hooks + SDK wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from headroom import compress as _headroom_compress
    from headroom.hooks import CompressContext, CompressEvent, CompressionHooks

    _HEADROOM_AVAILABLE = True
except ImportError:
    _HEADROOM_AVAILABLE = False

    class CompressionHooks:  # type: ignore[no-redef]
        def pre_compress(self, messages, ctx):
            return messages

        def compute_biases(self, messages, ctx):
            return {}

        def post_compress(self, event):
            pass

    class CompressContext:  # type: ignore[no-redef]
        model: str = ""

    class CompressEvent:  # type: ignore[no-redef]
        tokens_before: int = 0
        tokens_after: int = 0


def is_available() -> bool:
    return _HEADROOM_AVAILABLE


@dataclass(frozen=True, slots=True)
class CompressionResult:
    messages: list[dict[str, Any]]
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    compression_ratio: float
    applied: bool


class CostwiseCompressionHooks(CompressionHooks):
    """Graph-aware compression biases for Headroom.

    High-relevance messages (per graph scoring) get less aggressive compression.
    Low-relevance messages get compressed harder.
    """

    def __init__(self, relevance_scores: dict[int, float] | None = None) -> None:
        self._relevance_scores = relevance_scores or {}
        self._last_event: CompressEvent | None = None

    def compute_biases(
        self,
        messages: list[dict[str, Any]],
        ctx: Any,
    ) -> dict[int, float]:
        if not self._relevance_scores:
            return {}

        biases: dict[int, float] = {}
        for idx, score in self._relevance_scores.items():
            if 0 <= idx < len(messages):
                biases[idx] = 0.5 + score * 1.5
        return biases

    def post_compress(self, event: Any) -> None:
        self._last_event = event

    @property
    def last_event(self) -> Any:
        return self._last_event


def compress_messages(
    messages: list[dict[str, Any]],
    model: str,
    relevance_scores: dict[int, float] | None = None,
) -> CompressionResult:
    """Compress messages using Headroom with graph-aware biases.

    Falls back to passthrough if Headroom is not installed.
    """
    if not _HEADROOM_AVAILABLE:
        token_est = sum(len(str(m.get("content", ""))) // 4 for m in messages)
        return CompressionResult(
            messages=messages,
            tokens_before=token_est,
            tokens_after=token_est,
            tokens_saved=0,
            compression_ratio=0.0,
            applied=False,
        )

    hooks = CostwiseCompressionHooks(relevance_scores)

    try:
        result = _headroom_compress(messages, model=model, hooks=hooks)
        return CompressionResult(
            messages=result.messages,
            tokens_before=result.tokens_before if hasattr(result, "tokens_before") else 0,
            tokens_after=result.tokens_after if hasattr(result, "tokens_after") else 0,
            tokens_saved=result.tokens_saved if hasattr(result, "tokens_saved") else 0,
            compression_ratio=result.compression_ratio if hasattr(result, "compression_ratio") else 0.0,
            applied=True,
        )
    except Exception:
        logger.warning("Headroom compression failed, passing through", exc_info=True)
        token_est = sum(len(str(m.get("content", ""))) // 4 for m in messages)
        return CompressionResult(
            messages=messages,
            tokens_before=token_est,
            tokens_after=token_est,
            tokens_saved=0,
            compression_ratio=0.0,
            applied=False,
        )
