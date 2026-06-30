"""In-memory graph cache with file-change reload."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from costwise.graph.loader import CodeGraph, load_graph

logger = logging.getLogger(__name__)

_STALE_CHECK_INTERVAL_S = 2.0


class GraphCache:
    """Thread-safe cached CodeGraph that reloads when graph.json changes."""

    def __init__(self, graph_path: str | Path | None = None) -> None:
        self._path = Path(graph_path) if graph_path else None
        self._graph: CodeGraph | None = None
        self._mtime: float = 0.0
        self._last_check: float = 0.0
        self._lock = threading.Lock()
        self._load_error: str | None = None

    @property
    def is_available(self) -> bool:
        return self._graph is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def configure(self, graph_path: str | Path) -> None:
        """Set or change the graph path. Triggers a reload on next access."""
        with self._lock:
            self._path = Path(graph_path)
            self._mtime = 0.0
            self._graph = None
            self._load_error = None

    def get(self) -> CodeGraph | None:
        """Get the current graph, reloading if the file changed."""
        if self._path is None:
            return None

        now = time.monotonic()
        if now - self._last_check < _STALE_CHECK_INTERVAL_S and self._graph is not None:
            return self._graph

        with self._lock:
            self._last_check = now
            return self._maybe_reload()

    def _maybe_reload(self) -> CodeGraph | None:
        if self._path is None:
            return None

        if not self._path.exists():
            if self._graph is not None:
                logger.info("Graph file removed: %s", self._path)
                self._graph = None
                self._mtime = 0.0
            return None

        try:
            current_mtime = self._path.stat().st_mtime
        except OSError:
            return self._graph

        if current_mtime == self._mtime and self._graph is not None:
            return self._graph

        try:
            graph = load_graph(self._path)
            self._graph = graph
            self._mtime = current_mtime
            self._load_error = None
            logger.info(
                "Loaded graph: %d nodes, %d edges from %s",
                graph.node_count, graph.edge_count, self._path,
            )
        except Exception as e:
            self._load_error = str(e)
            logger.warning("Failed to load graph from %s: %s", self._path, e)

        return self._graph

    def invalidate(self) -> None:
        """Force a reload on next access."""
        with self._lock:
            self._mtime = 0.0
            self._last_check = 0.0

    def clear(self) -> None:
        """Remove the cached graph entirely."""
        with self._lock:
            self._graph = None
            self._mtime = 0.0
            self._load_error = None
