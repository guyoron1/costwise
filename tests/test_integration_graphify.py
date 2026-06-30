"""Tests for Graphify MCP client integration."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from costwise.integrations.graphify import GraphifyClient, GraphQueryResult


@pytest.fixture()
def client():
    return GraphifyClient("/tmp/test-graph.json")


class TestGraphifyClient:
    def test_init(self, client):
        assert client._graph_path == "/tmp/test-graph.json"
        assert not client.running

    def test_not_running_initially(self, client):
        assert client.running is False

    async def test_close_when_not_started(self, client):
        await client.close()
        assert client.running is False

    async def test_query_graph_parses_response(self, client):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n"
            return json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [{"type": "text", "text": "Found: AuthService → UserRepo"}]
                },
            }).encode() + b"\n"

        mock_proc.stdout.readline = mock_readline

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await client.query_graph("authentication flow")

        assert result is not None
        assert isinstance(result, GraphQueryResult)
        assert "AuthService" in result.text
        assert result.tool_name == "query_graph"

    async def test_get_node_returns_result(self, client):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n"
            return json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [{"type": "text", "text": "Node: Router (5 edges)"}]
                },
            }).encode() + b"\n"

        mock_proc.stdout.readline = mock_readline

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await client.get_node("Router")

        assert result is not None
        assert result.tool_name == "get_node"
        assert "Router" in result.text

    async def test_handles_mcp_error(self, client):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n"
            return json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "error": {"code": -32600, "message": "Invalid request"},
            }).encode() + b"\n"

        mock_proc.stdout.readline = mock_readline

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await client.query_graph("test")

        assert result is None

    async def test_close_terminates_process(self, client):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        client._process = mock_proc
        await client.close()

        mock_proc.terminate.assert_called_once()
        assert client._process is None

    async def test_get_neighbors_with_filter(self, client):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n"
            return json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "Neighbors: A, B"}]},
            }).encode() + b"\n"

        mock_proc.stdout.readline = mock_readline

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await client.get_neighbors("Router", relation_filter="calls")

        assert result is not None
        assert result.tool_name == "get_neighbors"

    async def test_graph_stats(self, client):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n"
            return json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "Nodes: 42, Edges: 87"}]},
            }).encode() + b"\n"

        mock_proc.stdout.readline = mock_readline

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await client.graph_stats()

        assert result is not None
        assert "42" in result.text
