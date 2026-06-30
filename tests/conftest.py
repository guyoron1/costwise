"""Shared fixtures for Costwise tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from costwise.graph.loader import CodeGraph, load_graph

GRAPHIFY_HTTPX = Path(__file__).parent / "fixtures" / "httpx_graph.json"
GRAPHIFY_WORKED = Path.home() / "Desktop" / "graphify" / "worked" / "httpx" / "graph.json"


@pytest.fixture(scope="session")
def httpx_graph_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provide a path to a real Graphify graph.json for testing."""
    if GRAPHIFY_HTTPX.exists():
        return GRAPHIFY_HTTPX
    if GRAPHIFY_WORKED.exists():
        dest = tmp_path_factory.mktemp("fixtures") / "graph.json"
        dest.write_text(GRAPHIFY_WORKED.read_text())
        return dest
    pytest.skip("No Graphify graph.json fixture available")


@pytest.fixture(scope="session")
def httpx_graph(httpx_graph_path: Path) -> CodeGraph:
    return load_graph(httpx_graph_path)
