"""Content fingerprinting for retry detection.

Produces a deterministic hash from user messages and provides
fuzzy similarity comparison for detecting rephrased retries.
"""

from __future__ import annotations

import hashlib
import re

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def _extract_user_text(messages: list[dict]) -> str:
    """Extract text from the last user message."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            return " ".join(parts)
    return ""


def _normalize(text: str) -> str:
    """Normalize text for hashing: lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = _PUNCT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def fingerprint(messages: list[dict]) -> str:
    """Produce a SHA-256 hash from the last user message, normalized."""
    text = _extract_user_text(messages)
    normalized = _normalize(text)
    return hashlib.sha256(normalized.encode()).hexdigest()


def _word_set(text: str) -> set[str]:
    """Extract word set from normalized text."""
    normalized = _normalize(text)
    return set(normalized.split()) if normalized else set()


def similarity(messages_a: list[dict], messages_b: list[dict]) -> float:
    """Compute similarity between two message lists for retry detection.

    Uses a two-tier approach:
    1. Exact hash match → 1.0
    2. Word-set Jaccard similarity on the last user message

    Returns 0.0..1.0.
    """
    fp_a = fingerprint(messages_a)
    fp_b = fingerprint(messages_b)
    if fp_a == fp_b:
        return 1.0

    text_a = _extract_user_text(messages_a)
    text_b = _extract_user_text(messages_b)
    words_a = _word_set(text_a)
    words_b = _word_set(text_b)

    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
