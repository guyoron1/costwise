"""Remove low-relevance context entries from LLM request messages."""

from __future__ import annotations

from dataclasses import dataclass

from costwise.graph.loader import CodeGraph
from costwise.graph.relevance import RelevanceResult, extract_references, score_relevance


@dataclass
class PruneResult:
    """Outcome of context pruning."""

    original_messages: int
    pruned_messages: int
    original_token_estimate: int
    pruned_token_estimate: int
    dropped_entries: int

    @property
    def tokens_saved(self) -> int:
        return self.original_token_estimate - self.pruned_token_estimate

    @property
    def reduction_pct(self) -> float:
        if self.original_token_estimate == 0:
            return 0.0
        return (self.tokens_saved / self.original_token_estimate) * 100


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _score_message(msg: dict, relevance: RelevanceResult, graph: CodeGraph) -> float:
    """Score a message's relevance based on file references in its content."""
    content = msg.get("content", "")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )

    if not text:
        return 1.0

    files, symbols = extract_references(text)
    if not files and not symbols:
        return 0.5

    file_scores = [relevance.score_for_file(f, graph) for f in files]
    node_scores = []
    for sym in symbols:
        parts = sym.lower().replace(".", "_")
        for nid in relevance.scores:
            if parts in nid.lower():
                node_scores.append(relevance.scores[nid])
                break

    all_scores = file_scores + node_scores
    if not all_scores:
        return 0.0

    return max(all_scores)


def _prune_content_blocks(
    content: list[dict],
    relevance: RelevanceResult,
    graph: CodeGraph,
    threshold: float,
) -> tuple[list[dict], int]:
    """Prune individual content blocks within a message. Returns (kept_blocks, dropped_count)."""
    kept: list[dict] = []
    dropped = 0

    for block in content:
        if not isinstance(block, dict):
            kept.append(block)
            continue

        text = block.get("text", "")
        if not text:
            kept.append(block)
            continue

        files, symbols = extract_references(text)
        if not files and not symbols:
            kept.append(block)
            continue

        file_scores = [relevance.score_for_file(f, graph) for f in files]
        all_scores = file_scores if file_scores else [0.0]
        max_score = max(all_scores)

        if max_score >= threshold:
            kept.append(block)
        else:
            dropped += 1

    return kept, dropped


def prune_context(
    messages: list[dict],
    graph: CodeGraph,
    *,
    threshold: float = 0.1,
    max_hops: int = 4,
    decay: float = 0.5,
    community_boost: float = 0.2,
    protect_roles: frozenset[str] = frozenset({"system"}),
    protect_last_n: int = 2,
) -> tuple[list[dict], PruneResult]:
    """Prune low-relevance context from messages using graph-guided scoring.

    Args:
        messages: The messages array from an LLM API request.
        graph: Loaded CodeGraph from Graphify.
        threshold: Minimum relevance score to keep a message (0-1).
        max_hops: Max BFS depth from seed nodes.
        decay: Relevance decay per hop (0-1).
        community_boost: Bonus for same-community nodes.
        protect_roles: Message roles that are never pruned.
        protect_last_n: Always keep the last N messages (user's current turn).

    Returns:
        Tuple of (pruned_messages, PruneResult metadata).
    """
    # Estimate original token count from all messages
    all_text_parts: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            all_text_parts.append(content)
        elif isinstance(content, list):
            all_text_parts.extend(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
    original_tokens = _estimate_tokens("\n".join(all_text_parts))

    # Score relevance from the LAST user message only (the current intent),
    # not the entire history — otherwise every file ever mentioned becomes a seed
    focus_text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                focus_text = content
            elif isinstance(content, list):
                focus_text = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            break

    relevance = score_relevance(
        graph, focus_text,
        max_hops=max_hops,
        decay=decay,
        community_boost=community_boost,
    )

    if not relevance.seed_nodes:
        return messages, PruneResult(
            original_messages=len(messages),
            pruned_messages=len(messages),
            original_token_estimate=original_tokens,
            pruned_token_estimate=original_tokens,
            dropped_entries=0,
        )

    pruned: list[dict] = []
    total_dropped = 0
    protected_indices = set(range(max(0, len(messages) - protect_last_n), len(messages)))

    for i, msg in enumerate(messages):
        role = msg.get("role", "")

        if role in protect_roles or i in protected_indices:
            pruned.append(msg)
            continue

        content = msg.get("content", "")

        if isinstance(content, list) and len(content) > 1:
            kept_blocks, dropped = _prune_content_blocks(
                content, relevance, graph, threshold,
            )
            total_dropped += dropped
            if kept_blocks:
                pruned.append({**msg, "content": kept_blocks})
            else:
                total_dropped += 1
            continue

        score = _score_message(msg, relevance, graph)
        if score >= threshold:
            pruned.append(msg)
        else:
            total_dropped += 1

    pruned_text_parts: list[str] = []
    for msg in pruned:
        content = msg.get("content", "")
        if isinstance(content, str):
            pruned_text_parts.append(content)
        elif isinstance(content, list):
            pruned_text_parts.extend(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
    pruned_tokens = _estimate_tokens("\n".join(pruned_text_parts))

    return pruned, PruneResult(
        original_messages=len(messages),
        pruned_messages=len(pruned),
        original_token_estimate=original_tokens,
        pruned_token_estimate=pruned_tokens,
        dropped_entries=total_dropped,
    )
