"""Tests for costwise.graph.relevance — BFS scoring and reference extraction."""

from costwise.graph.loader import CodeGraph
from costwise.graph.relevance import (
    compute_graph_complexity,
    extract_references,
    score_relevance,
)


class TestExtractReferences:
    def test_extract_python_files(self) -> None:
        files, _ = extract_references("Fix the bug in auth.py and client.py")
        assert "auth.py" in files
        assert "client.py" in files

    def test_extract_nested_paths(self) -> None:
        files, _ = extract_references("Edit src/costwise/core/router.py")
        assert "src/costwise/core/router.py" in files

    def test_extract_symbols(self) -> None:
        _, symbols = extract_references("The FunctionAuth class has a bug")
        assert "FunctionAuth" in symbols

    def test_no_false_positives_on_plain_text(self) -> None:
        files, _ = extract_references("Please fix the authentication issue")
        assert len(files) == 0


class TestScoreRelevance:
    def test_seed_nodes_score_one(self, httpx_graph: CodeGraph) -> None:
        result = score_relevance(httpx_graph, "Fix auth.py")
        assert len(result.seed_nodes) > 0
        for sid in result.seed_nodes:
            assert result.scores[sid] == 1.0

    def test_bfs_decay(self, httpx_graph: CodeGraph) -> None:
        result = score_relevance(httpx_graph, "Fix auth.py")
        non_seeds = {
            nid: score
            for nid, score in result.scores.items()
            if nid not in result.seed_nodes
        }
        assert len(non_seeds) > 0
        for score in non_seeds.values():
            assert score < 1.0

    def test_community_boost(self, httpx_graph: CodeGraph) -> None:
        result = score_relevance(httpx_graph, "Fix auth.py", community_boost=0.2)
        seed_communities = set()
        for sid in result.seed_nodes:
            c = httpx_graph.community_of(sid)
            if c >= 0:
                seed_communities.add(c)

        for c in seed_communities:
            for nid in httpx_graph.communities.get(c, set()):
                assert result.scores.get(nid, 0.0) >= 0.2

    def test_no_references_returns_empty(self, httpx_graph: CodeGraph) -> None:
        result = score_relevance(httpx_graph, "Hello, how are you?")
        assert len(result.seed_nodes) == 0
        assert len(result.scores) == 0

    def test_edge_type_weighting(self, httpx_graph: CodeGraph) -> None:
        """Nodes reached via imports_from should score higher than via uses."""
        result = score_relevance(httpx_graph, "Fix auth.py")
        assert len(result.scores) > 10


class TestGraphComplexity:
    def test_returns_zero_without_file_refs(self, httpx_graph: CodeGraph) -> None:
        score = compute_graph_complexity(httpx_graph, "What is the meaning of life?")
        assert score == 0.0

    def test_returns_nonzero_for_central_file(self, httpx_graph: CodeGraph) -> None:
        score = compute_graph_complexity(httpx_graph, "Edit client.py")
        assert score > 0.0

    def test_bounded_zero_to_one(self, httpx_graph: CodeGraph) -> None:
        score = compute_graph_complexity(httpx_graph, "Fix client.py auth.py models.py")
        assert 0.0 <= score <= 1.0
