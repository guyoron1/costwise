"""Graphify integration — MCP client for dynamic graph queries."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_GRAPH_PATH = "graphify-out/graph.json"


@dataclass(frozen=True, slots=True)
class GraphQueryResult:
    text: str
    nodes_visited: int
    tool_name: str


class GraphifyClient:
    """Async MCP client for Graphify's stdio server.

    Spawns `python -m graphify.serve <graph_path>` as a subprocess and
    communicates via JSON-RPC over stdio (MCP protocol).
    """

    def __init__(self, graph_path: str | Path = _DEFAULT_GRAPH_PATH) -> None:
        self._graph_path = str(graph_path)
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self.running and self._process is not None:
            return self._process

        async with self._lock:
            if self.running and self._process is not None:
                return self._process

            self._process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "graphify.serve", self._graph_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if not self._initialized:
                await self._send_initialize()
                self._initialized = True

            return self._process

    async def _send_initialize(self) -> None:
        await self._rpc_call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "costwise", "version": "0.1.0"},
        })
        await self._rpc_notify("notifications/initialized", {})

    async def _rpc_call(self, method: str, params: dict) -> dict | None:
        proc = await self._ensure_started()
        if proc.stdin is None or proc.stdout is None:
            return None

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        line = json.dumps(request) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
        if not raw:
            return None

        response = json.loads(raw.decode())
        if "error" in response:
            logger.warning("MCP error from Graphify: %s", response["error"])
            return None
        return response.get("result")

    async def _rpc_notify(self, method: str, params: dict) -> None:
        proc = await self._ensure_started()
        if proc.stdin is None:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

    async def _call_tool(self, tool_name: str, arguments: dict) -> str | None:
        result = await self._rpc_call("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if not result:
            return None

        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else None

    async def query_graph(
        self,
        question: str,
        *,
        mode: str = "bfs",
        depth: int = 3,
        token_budget: int = 2000,
    ) -> GraphQueryResult | None:
        text = await self._call_tool("query_graph", {
            "question": question,
            "mode": mode,
            "depth": depth,
            "token_budget": token_budget,
        })
        if text is None:
            return None
        return GraphQueryResult(text=text, nodes_visited=0, tool_name="query_graph")

    async def get_node(self, label: str) -> GraphQueryResult | None:
        text = await self._call_tool("get_node", {"label": label})
        if text is None:
            return None
        return GraphQueryResult(text=text, nodes_visited=1, tool_name="get_node")

    async def get_neighbors(
        self, label: str, relation_filter: str | None = None,
    ) -> GraphQueryResult | None:
        args: dict = {"label": label}
        if relation_filter:
            args["relation_filter"] = relation_filter
        text = await self._call_tool("get_neighbors", args)
        if text is None:
            return None
        return GraphQueryResult(text=text, nodes_visited=0, tool_name="get_neighbors")

    async def get_community(self, community_id: int) -> GraphQueryResult | None:
        text = await self._call_tool("get_community", {"community_id": community_id})
        if text is None:
            return None
        return GraphQueryResult(text=text, nodes_visited=0, tool_name="get_community")

    async def graph_stats(self) -> GraphQueryResult | None:
        text = await self._call_tool("graph_stats", {})
        if text is None:
            return None
        return GraphQueryResult(text=text, nodes_visited=0, tool_name="graph_stats")

    async def close(self) -> None:
        if self._process is not None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                self._process.kill()
            finally:
                self._process = None
                self._initialized = False
