"""BFS relevance scoring with community awareness and edge-type weighting."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from costwise.graph.loader import EDGE_WEIGHTS, CodeGraph

# Matches file paths in text: word/word.ext or word.ext patterns
_FILE_PATH_RE = re.compile(
    r"(?:^|[\s\"'`(,])("
    r"(?:[\w.-]+/)+[\w.-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|c|cpp|h|hpp|cs|swift|kt)"
    r"|[\w.-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|c|cpp|h|hpp|cs|swift|kt)"
    r")",
    re.MULTILINE,
)

# Matches symbol names: CamelCase identifiers or dotted paths
_SYMBOL_RE = re.compile(
    r"\b([A-Z][a-zA-Z0-9]+(?:\.[a-zA-Z_]\w*)*)\b"
    r"|\b(\w+\.\w+(?:\.\w+)+)\b"
)


@dataclass
class RelevanceResult:
    """Per-node relevance scores and metadata."""

    scores: dict[str, float] = field(default_factory=dict)
    seed_nodes: list[str] = field(default_factory=list)
    files_found: list[str] = field(default_factory=list)

    def score_for_file(self, file_path: str, graph: CodeGraph) -> float:
        """Get the max relevance score for any node associated with a file."""
        node_ids = graph.nodes_for_file(file_path)
        if not node_ids:
            return 0.0
        return max(self.scores.get(nid, 0.0) for nid in node_ids)

    def above_threshold(self, threshold: float) -> dict[str, float]:
        return {nid: score for nid, score in self.scores.items() if score >= threshold}


def compute_graph_complexity(graph: CodeGraph, text: str) -> float:
    """Compute a 0-1 complexity score based on graph centrality of referenced files.

    High scores mean the prompt references central, highly-connected code.
    """
    file_refs, _ = extract_references(text)
    if not file_refs:
        return 0.0

    degrees: list[int] = []
    for fref in file_refs:
        for nid in graph.nodes_for_file(fref):
            degrees.append(len(graph.neighbors(nid)))

    if not degrees:
        return 0.0

    max_degree = max(len(graph.neighbors(nid)) for nid in graph.nodes) if graph.nodes else 1
    if max_degree == 0:
        return 0.0

    avg_degree = sum(degrees) / len(degrees)
    return min(1.0, avg_degree / max_degree)


def extract_references(text: str) -> tuple[list[str], list[str]]:
    """Extract file paths and symbol names from text."""
    files = [m.strip() for m in _FILE_PATH_RE.findall(text) if m.strip()]
    symbols = []
    for m in _SYMBOL_RE.finditer(text):
        sym = m.group(1) or m.group(2)
        if sym:
            symbols.append(sym)
    return files, symbols


def _find_seed_nodes(
    graph: CodeGraph,
    file_refs: list[str],
    symbol_refs: list[str],
) -> list[str]:
    """Map file paths and symbols to graph node IDs."""
    seeds: set[str] = set()

    for fref in file_refs:
        node_ids = graph.nodes_for_file(fref)
        seeds.update(node_ids)

    for sym in symbol_refs:
        parts = sym.lower().replace(".", "_")
        for nid, node in graph.nodes.items():
            if parts in nid.lower() or parts in node.label.lower():
                seeds.add(nid)

    return list(seeds)


def score_relevance(
    graph: CodeGraph,
    text: str,
    *,
    max_hops: int = 4,
    decay: float = 0.5,
    community_boost: float = 0.2,
    min_edge_weight: float = 0.0,
) -> RelevanceResult:
    """Score node relevance using BFS from references found in text.

    Algorithm:
      1. Extract file paths and symbols from the text
      2. Find matching seed nodes in the graph (relevance = 1.0)
      3. BFS outward: each hop multiplies relevance by decay * edge_type_weight
      4. Nodes sharing a Leiden community with any seed get +community_boost
      5. Scores are clamped to [0, 1]
    """
    file_refs, symbol_refs = extract_references(text)
    seeds = _find_seed_nodes(graph, file_refs, symbol_refs)

    if not seeds:
        return RelevanceResult(files_found=file_refs)

    scores: dict[str, float] = {}
    seed_communities: set[int] = set()

    for sid in seeds:
        scores[sid] = 1.0
        c = graph.community_of(sid)
        if c >= 0:
            seed_communities.add(c)

    # BFS with decay
    queue: deque[tuple[str, int, float]] = deque()
    for sid in seeds:
        queue.append((sid, 0, 1.0))

    visited_from: dict[str, set[str]] = {sid: {sid} for sid in seeds}

    while queue:
        current, depth, current_score = queue.popleft()

        if depth >= max_hops:
            continue

        for neighbor_id, edge in graph.neighbors(current):
            edge_type_weight = EDGE_WEIGHTS.get(edge.relation, 0.5)
            if edge_type_weight < min_edge_weight:
                continue

            propagated = current_score * decay * edge_type_weight

            if propagated < 0.01:
                continue

            existing = scores.get(neighbor_id, 0.0)
            if propagated > existing:
                scores[neighbor_id] = propagated
                if neighbor_id not in visited_from:
                    visited_from[neighbor_id] = set()
                visited_from[neighbor_id].add(current)
                queue.append((neighbor_id, depth + 1, propagated))

    # Community boost: nodes in same community as seeds get a bump
    if seed_communities:
        for community_id in seed_communities:
            for nid in graph.communities.get(community_id, set()):
                if nid in scores:
                    scores[nid] = min(1.0, scores[nid] + community_boost)
                else:
                    scores[nid] = community_boost

    return RelevanceResult(
        scores=scores,
        seed_nodes=seeds,
        files_found=file_refs,
    )
