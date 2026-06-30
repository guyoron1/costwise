"""Parse Graphify's graph.json (NetworkX node-link format) into an in-memory CodeGraph."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    label: str
    source_file: str
    source_location: str = ""
    community: int = -1
    file_type: str = "code"


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    weight: float = 1.0
    confidence: str = "EXTRACTED"


# Edge-type weights for relevance scoring — tighter coupling = higher weight
EDGE_WEIGHTS: dict[str, float] = {
    "imports_from": 1.0,
    "inherits": 0.95,
    "calls": 0.85,
    "method": 0.8,
    "contains": 0.7,
    "uses": 0.6,
    "semantically_similar_to": 0.4,
}


@dataclass
class CodeGraph:
    """In-memory code knowledge graph built from Graphify output."""

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    adjacency: dict[str, list[tuple[str, GraphEdge]]] = field(default_factory=dict)
    file_to_nodes: dict[str, list[str]] = field(default_factory=dict)
    communities: dict[int, set[str]] = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(neighbors) for neighbors in self.adjacency.values()) // 2

    def neighbors(self, node_id: str) -> list[tuple[str, GraphEdge]]:
        return self.adjacency.get(node_id, [])

    def nodes_for_file(self, file_path: str) -> list[str]:
        """Find graph nodes associated with a file path (suffix match)."""
        normalized = _normalize_path(file_path)
        if normalized in self.file_to_nodes:
            return self.file_to_nodes[normalized]
        for stored_path, node_ids in self.file_to_nodes.items():
            if stored_path.endswith(normalized) or normalized.endswith(stored_path):
                return node_ids
        return []

    def community_of(self, node_id: str) -> int:
        node = self.nodes.get(node_id)
        return node.community if node else -1

    def same_community(self, node_a: str, node_b: str) -> bool:
        ca = self.community_of(node_a)
        cb = self.community_of(node_b)
        return ca >= 0 and ca == cb


def _normalize_path(path: str) -> str:
    """Strip leading ./ and normalize separators for matching."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def load_graph(path: str | Path) -> CodeGraph:
    """Load a Graphify graph.json file into a CodeGraph."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    graph = CodeGraph()

    for raw_node in raw.get("nodes", []):
        node = GraphNode(
            id=raw_node["id"],
            label=raw_node.get("label", raw_node["id"]),
            source_file=_normalize_path(raw_node.get("source_file", "")),
            source_location=raw_node.get("source_location", ""),
            community=raw_node.get("community", -1),
            file_type=raw_node.get("file_type", "code"),
        )
        graph.nodes[node.id] = node

        if node.source_file:
            graph.file_to_nodes.setdefault(node.source_file, []).append(node.id)

        if node.community >= 0:
            graph.communities.setdefault(node.community, set()).add(node.id)

    for raw_edge in raw.get("links", []):
        src = raw_edge.get("source", raw_edge.get("_src", ""))
        tgt = raw_edge.get("target", raw_edge.get("_tgt", ""))
        if not src or not tgt:
            continue
        if src not in graph.nodes or tgt not in graph.nodes:
            continue

        relation = raw_edge.get("relation", "uses")
        edge = GraphEdge(
            source=src,
            target=tgt,
            relation=relation,
            weight=raw_edge.get("weight", EDGE_WEIGHTS.get(relation, 0.5)),
            confidence=raw_edge.get("confidence", "EXTRACTED"),
        )

        graph.adjacency.setdefault(src, []).append((tgt, edge))
        if not raw.get("directed", False):
            reverse = GraphEdge(
                source=tgt, target=src,
                relation=relation, weight=edge.weight, confidence=edge.confidence,
            )
            graph.adjacency.setdefault(tgt, []).append((src, reverse))

    return graph
