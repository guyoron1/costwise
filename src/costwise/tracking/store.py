"""SQLite tracking store for routing decisions and cost data."""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RoutingRecord:
    endpoint: str
    request_model: str
    session_id: str | None = None
    routed_model: str | None = None
    tier: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    saved_usd: float | None = None
    latency_ms: float | None = None
    classification: str | None = None
    provider: str | None = None
    status_code: int | None = None
    error: str | None = None
    tokens_pruned: int | None = None
    messages_pruned: int | None = None
    content_hash: str | None = None


def _schema_sql() -> str:
    return importlib.resources.files("costwise.tracking").joinpath("schema.sql").read_text()


class TrackingStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_schema_sql())
            self._migrate()
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _migrate(self) -> None:
        assert self._conn is not None
        try:
            self._conn.execute("ALTER TABLE routing_decisions ADD COLUMN content_hash TEXT")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_routing_content_hash "
                "ON routing_decisions(session_id, content_hash)"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

    async def initialize(self) -> None:
        await asyncio.to_thread(self._get_conn)

    async def record_request(self, record: RoutingRecord) -> int:
        def _insert() -> int:
            conn = self._get_conn()
            cursor = conn.execute(
                """INSERT INTO routing_decisions
                   (session_id, request_model, routed_model, tier,
                    prompt_tokens, completion_tokens, total_tokens,
                    cost_usd, saved_usd, latency_ms,
                    classification, provider, endpoint, status_code, error,
                    tokens_pruned, messages_pruned, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.session_id, record.request_model, record.routed_model,
                    record.tier, record.prompt_tokens, record.completion_tokens,
                    record.total_tokens, record.cost_usd, record.saved_usd,
                    record.latency_ms, record.classification, record.provider,
                    record.endpoint, record.status_code, record.error,
                    record.tokens_pruned, record.messages_pruned, record.content_hash,
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

        return await asyncio.to_thread(_insert)

    async def get_session_stats(self, session_id: str | None = None) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            if session_id:
                rows = conn.execute(
                    """SELECT request_model, routed_model,
                              SUM(prompt_tokens) as total_prompt,
                              SUM(completion_tokens) as total_completion,
                              SUM(total_tokens) as total_tokens,
                              SUM(cost_usd) as total_cost,
                              SUM(saved_usd) as total_saved,
                              COUNT(*) as request_count
                       FROM routing_decisions
                       WHERE session_id = ?
                       GROUP BY request_model, routed_model""",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT request_model, routed_model,
                              SUM(prompt_tokens) as total_prompt,
                              SUM(completion_tokens) as total_completion,
                              SUM(total_tokens) as total_tokens,
                              SUM(cost_usd) as total_cost,
                              SUM(saved_usd) as total_saved,
                              COUNT(*) as request_count
                       FROM routing_decisions
                       GROUP BY request_model, routed_model""",
                ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def get_recent_requests(self, limit: int = 20) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT timestamp, request_model, routed_model, tier,
                          prompt_tokens, completion_tokens, total_tokens,
                          cost_usd, saved_usd, latency_ms, endpoint, status_code
                   FROM routing_decisions
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def record_provider_health(
        self,
        provider: str,
        model: str,
        latency_ms: float,
        status_code: int,
        rate_limited: bool = False,
        error: str | None = None,
    ) -> None:
        def _insert() -> None:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO provider_health
                   (provider, model, latency_ms, status_code, rate_limited, error)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider, model, latency_ms, status_code, int(rate_limited), error),
            )
            conn.commit()

        await asyncio.to_thread(_insert)

    async def record_budget_alert(
        self,
        alert_type: str,
        threshold_usd: float | None,
        current_usd: float,
        action_taken: str,
    ) -> None:
        def _insert() -> None:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO budget_alerts
                   (alert_type, threshold_usd, current_usd, action_taken)
                   VALUES (?, ?, ?, ?)""",
                (alert_type, threshold_usd, current_usd, action_taken),
            )
            conn.commit()

        await asyncio.to_thread(_insert)

    async def get_hourly_spend(self) -> float:
        def _query() -> float:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0.0) as total
                   FROM routing_decisions
                   WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', '-1 hour'))""",
            ).fetchone()
            return float(row["total"]) if row else 0.0

        return await asyncio.to_thread(_query)

    async def get_session_spend(self, session_id: str) -> float:
        def _query() -> float:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0.0) as total
                   FROM routing_decisions
                   WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            return float(row["total"]) if row else 0.0

        return await asyncio.to_thread(_query)

    async def get_provider_health_stats(
        self, provider: str, window_minutes: int = 5
    ) -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(rate_limited) as rate_limits,
                          SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as errors,
                          AVG(latency_ms) as avg_latency,
                          MAX(latency_ms) as max_latency
                   FROM provider_health
                   WHERE provider = ?
                     AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' minutes'))""",
                (provider, str(-window_minutes)),
            ).fetchone()
            return dict(row) if row else {}

        return await asyncio.to_thread(_query)

    async def get_gain_summary(self) -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT COUNT(*) as total_requests,
                          SUM(prompt_tokens) as total_prompt_tokens,
                          SUM(completion_tokens) as total_completion_tokens,
                          SUM(total_tokens) as total_tokens,
                          SUM(cost_usd) as total_cost_usd,
                          SUM(saved_usd) as total_saved_usd,
                          MIN(timestamp) as first_request,
                          MAX(timestamp) as last_request
                   FROM routing_decisions""",
            ).fetchone()
            return dict(row) if row else {}

        return await asyncio.to_thread(_query)

    async def get_model_distribution(self, since: str | None = None) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            if since:
                rows = conn.execute(
                    """SELECT routed_model, COUNT(*) as count,
                              SUM(cost_usd) as total_cost,
                              SUM(saved_usd) as total_saved,
                              SUM(total_tokens) as total_tokens
                       FROM routing_decisions
                       WHERE timestamp >= ?
                       GROUP BY routed_model
                       ORDER BY count DESC""",
                    (since,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT routed_model, COUNT(*) as count,
                              SUM(cost_usd) as total_cost,
                              SUM(saved_usd) as total_saved,
                              SUM(total_tokens) as total_tokens
                       FROM routing_decisions
                       GROUP BY routed_model
                       ORDER BY count DESC""",
                ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def get_tier_distribution(self, since: str | None = None) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            if since:
                rows = conn.execute(
                    """SELECT tier, COUNT(*) as count,
                              SUM(cost_usd) as total_cost,
                              SUM(saved_usd) as total_saved
                       FROM routing_decisions
                       WHERE timestamp >= ?
                       GROUP BY tier""",
                    (since,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT tier, COUNT(*) as count,
                              SUM(cost_usd) as total_cost,
                              SUM(saved_usd) as total_saved
                       FROM routing_decisions
                       GROUP BY tier""",
                ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def get_hourly_cost_series(self, hours: int = 24) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT strftime('%Y-%m-%dT%H:00', timestamp) as hour,
                          SUM(cost_usd) as cost,
                          SUM(saved_usd) as saved,
                          COUNT(*) as requests
                   FROM routing_decisions
                   WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' hours'))
                   GROUP BY hour
                   ORDER BY hour""",
                (str(-hours),),
            ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def get_savings_breakdown(self) -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT COALESCE(SUM(saved_usd), 0.0) as routing_saved_usd,
                          COALESCE(SUM(tokens_pruned), 0) as total_tokens_pruned,
                          COALESCE(SUM(messages_pruned), 0) as total_messages_pruned,
                          COALESCE(SUM(cost_usd), 0.0) as total_cost_usd,
                          COUNT(*) as total_requests
                   FROM routing_decisions""",
            ).fetchone()
            return dict(row) if row else {}

        return await asyncio.to_thread(_query)

    async def get_budget_alerts(self, limit: int = 10) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT timestamp, alert_type, threshold_usd,
                          current_usd, action_taken
                   FROM budget_alerts
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def record_retry_event(
        self,
        session_id: str,
        original_request_id: int,
        retry_request_id: int,
        content_hash: str,
        similarity_score: float,
        original_tier: str,
        original_model: str,
        time_delta_s: float,
        was_downgraded: bool = False,
    ) -> int:
        def _insert() -> int:
            conn = self._get_conn()
            cursor = conn.execute(
                """INSERT INTO retry_events
                   (session_id, original_request_id, retry_request_id,
                    content_hash, similarity_score, original_tier,
                    original_model, time_delta_s, was_downgraded)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, original_request_id, retry_request_id,
                    content_hash, similarity_score, original_tier,
                    original_model, time_delta_s, int(was_downgraded),
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

        return await asyncio.to_thread(_insert)

    async def record_threshold_adjustment(
        self,
        field: str,
        old_value: float,
        new_value: float,
        reason: str,
        retry_event_id: int | None = None,
        window_retry_rate: float | None = None,
        window_requests: int | None = None,
    ) -> None:
        def _insert() -> None:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO threshold_adjustments
                   (field, old_value, new_value, reason,
                    retry_event_id, window_retry_rate, window_requests)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (field, old_value, new_value, reason,
                 retry_event_id, window_retry_rate, window_requests),
            )
            conn.commit()

        await asyncio.to_thread(_insert)

    async def get_recent_fingerprints(
        self, session_id: str, window_minutes: int = 5, limit: int = 10,
    ) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id, timestamp, request_model, routed_model, tier,
                          content_hash, prompt_tokens, status_code
                   FROM routing_decisions
                   WHERE session_id = ?
                     AND content_hash IS NOT NULL
                     AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' minutes'))
                   ORDER BY id DESC LIMIT ?""",
                (session_id, str(-window_minutes), limit),
            ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def get_retry_rate(self, window_minutes: int = 60) -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            conn = self._get_conn()
            retries = conn.execute(
                """SELECT COUNT(*) as count FROM retry_events
                   WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' minutes'))""",
                (str(-window_minutes),),
            ).fetchone()
            total = conn.execute(
                """SELECT COUNT(*) as count FROM routing_decisions
                   WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' minutes'))""",
                (str(-window_minutes),),
            ).fetchone()
            retry_count = int(retries["count"]) if retries else 0
            total_count = int(total["count"]) if total else 0
            rate = retry_count / total_count if total_count > 0 else 0.0
            return {
                "retry_count": retry_count,
                "total_requests": total_count,
                "retry_rate": round(rate, 4),
                "window_minutes": window_minutes,
            }

        return await asyncio.to_thread(_query)

    async def get_false_downgrade_rate(self, window_minutes: int = 60) -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            conn = self._get_conn()
            downgrades = conn.execute(
                """SELECT COUNT(*) as count FROM routing_decisions
                   WHERE routed_model IS NOT NULL
                     AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' minutes'))""",
                (str(-window_minutes),),
            ).fetchone()
            false_downgrades = conn.execute(
                """SELECT COUNT(*) as count FROM retry_events
                   WHERE was_downgraded = 1
                     AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', ? || ' minutes'))""",
                (str(-window_minutes),),
            ).fetchone()
            downgrade_count = int(downgrades["count"]) if downgrades else 0
            false_count = int(false_downgrades["count"]) if false_downgrades else 0
            rate = false_count / downgrade_count if downgrade_count > 0 else 0.0
            return {
                "false_downgrade_count": false_count,
                "total_downgrades": downgrade_count,
                "false_downgrade_rate": round(rate, 4),
                "window_minutes": window_minutes,
            }

        return await asyncio.to_thread(_query)

    async def get_threshold_history(self, limit: int = 50) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT timestamp, field, old_value, new_value, reason,
                          window_retry_rate, window_requests
                   FROM threshold_adjustments
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def get_feedback_summary(self) -> dict[str, Any]:
        def _query() -> dict[str, Any]:
            conn = self._get_conn()
            retry_row = conn.execute(
                """SELECT COUNT(*) as count FROM retry_events
                   WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', '-1 hour'))""",
            ).fetchone()
            total_row = conn.execute(
                """SELECT COUNT(*) as count FROM routing_decisions
                   WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', '-1 hour'))""",
            ).fetchone()
            false_row = conn.execute(
                """SELECT COUNT(*) as count FROM retry_events
                   WHERE was_downgraded = 1
                     AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', '-1 hour'))""",
            ).fetchone()
            downgrade_row = conn.execute(
                """SELECT COUNT(*) as count FROM routing_decisions
                   WHERE routed_model IS NOT NULL
                     AND timestamp >= strftime('%Y-%m-%dT%H:%M:%fZ',
                         datetime('now', '-1 hour'))""",
            ).fetchone()
            adj_row = conn.execute(
                "SELECT COUNT(*) as count FROM threshold_adjustments",
            ).fetchone()

            retry_count = int(retry_row["count"]) if retry_row else 0
            total_count = int(total_row["count"]) if total_row else 0
            false_count = int(false_row["count"]) if false_row else 0
            downgrade_count = int(downgrade_row["count"]) if downgrade_row else 0
            adj_count = int(adj_row["count"]) if adj_row else 0

            retry_rate = retry_count / total_count if total_count > 0 else 0.0
            false_rate = false_count / downgrade_count if downgrade_count > 0 else 0.0

            return {
                "retry_count": retry_count,
                "retry_rate": round(retry_rate, 4),
                "false_downgrade_count": false_count,
                "false_downgrade_rate": round(false_rate, 4),
                "total_requests": total_count,
                "total_downgrades": downgrade_count,
                "total_threshold_adjustments": adj_count,
            }

        return await asyncio.to_thread(_query)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
