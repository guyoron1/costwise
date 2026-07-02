"""Tests for costwise.graph.loader — parsing Graphify's graph.json."""

from costwise.graph.loader import CodeGraph, GraphEdge, GraphNode


class TestCodeGraph:
    def test_load_node_count(self, httpx_graph: CodeGraph) -> None:
        assert httpx_graph.node_count == 144

    def test_load_edge_count(self, httpx_graph: CodeGraph) -> None:
        assert httpx_graph.edge_count == 330

    def test_nodes_have_required_fields(self, httpx_graph: CodeGraph) -> None:
        for node in httpx_graph.nodes.values():
            assert isinstance(node, GraphNode)
            assert node.id
            assert node.label
            # source_file may be empty for builtins (e.g., Exception)

    def test_file_to_nodes_index(self, httpx_graph: CodeGraph) -> None:
        assert len(httpx_graph.file_to_nodes) > 0
        client_nodes = httpx_graph.nodes_for_file("client.py")
        assert len(client_nodes) > 0
        for nid in client_nodes:
            assert "client" in httpx_graph.nodes[nid].source_file

    def test_communities_populated(self, httpx_graph: CodeGraph) -> None:
        assert len(httpx_graph.communities) > 0
        for community_id, members in httpx_graph.communities.items():
            assert community_id >= 0
            assert len(members) > 0

    def test_neighbors(self, httpx_graph: CodeGraph) -> None:
        neighbors = httpx_graph.neighbors("client")
        assert len(neighbors) > 0
        for nid, edge in neighbors:
            assert isinstance(edge, GraphEdge)
            assert edge.relation in (
                "imports_from", "calls", "uses",
                "contains", "method", "inherits",
            )

    def test_same_community(self, httpx_graph: CodeGraph) -> None:
        assert httpx_graph.same_community("client", "client_timeout")
        assert not httpx_graph.same_community("client", "nonexistent")

    def test_nodes_for_file_suffix_match(self, httpx_graph: CodeGraph) -> None:
        full = httpx_graph.nodes_for_file("worked/httpx/raw/auth.py")
        short = httpx_graph.nodes_for_file("auth.py")
        assert set(full) == set(short)

    def test_undirected_edges(self, httpx_graph: CodeGraph) -> None:
        """Edges should be bidirectional in undirected graph."""
        for src, neighbors in httpx_graph.adjacency.items():
            for tgt, edge in neighbors:
                reverse_neighbors = [n for n, _ in httpx_graph.neighbors(tgt)]
                assert src in reverse_neighbors, f"Missing reverse edge: {tgt} → {src}"
