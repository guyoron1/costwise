"""Tests for costwise.graph.cache — graph caching and reload."""

import json
from pathlib import Path

from costwise.graph.cache import GraphCache


class TestGraphCache:
    def test_loads_on_first_access(self, httpx_graph_path: Path) -> None:
        cache = GraphCache(httpx_graph_path)
        assert not cache.is_available
        graph = cache.get()
        assert graph is not None
        assert cache.is_available
        assert graph.node_count > 0

    def test_caches_between_calls(self, httpx_graph_path: Path) -> None:
        cache = GraphCache(httpx_graph_path)
        g1 = cache.get()
        g2 = cache.get()
        assert g1 is g2

    def test_invalidate_forces_reload(self, httpx_graph_path: Path) -> None:
        cache = GraphCache(httpx_graph_path)
        g1 = cache.get()
        cache.invalidate()
        g2 = cache.get()
        assert g1 is not g2
        assert g1.node_count == g2.node_count

    def test_clear_removes_graph(self, httpx_graph_path: Path) -> None:
        cache = GraphCache(httpx_graph_path)
        cache.get()
        assert cache.is_available
        cache.clear()
        assert not cache.is_available

    def test_missing_file_returns_none(self) -> None:
        cache = GraphCache("/nonexistent/graph.json")
        assert cache.get() is None

    def test_no_path_returns_none(self) -> None:
        cache = GraphCache()
        assert cache.get() is None

    def test_configure_changes_path(self, httpx_graph_path: Path) -> None:
        cache = GraphCache()
        assert cache.get() is None
        cache.configure(httpx_graph_path)
        graph = cache.get()
        assert graph is not None

    def test_reloads_on_file_change(self, httpx_graph_path: Path, tmp_path: Path) -> None:
        graph_file = tmp_path / "graph.json"
        data = json.loads(httpx_graph_path.read_text())
        graph_file.write_text(json.dumps(data))

        cache = GraphCache(graph_file)
        g1 = cache.get()
        assert g1 is not None
        original_count = g1.node_count

        data["nodes"] = data["nodes"][:10]
        node_ids = {n["id"] for n in data["nodes"]}
        data["links"] = [
            link for link in data["links"]
            if link["source"] in node_ids and link["target"] in node_ids
        ]
        graph_file.write_text(json.dumps(data))

        cache.invalidate()
        g2 = cache.get()
        assert g2 is not None
        assert g2.node_count < original_count
