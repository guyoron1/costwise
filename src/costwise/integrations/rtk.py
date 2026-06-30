"""RTK integration — read RTK's tracking DB for unified savings view."""

from __future__ import annotations

import os
import platform
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RtkSummary:
    total_commands: int
    total_input_tokens: int
    total_output_tokens: int
    total_saved_tokens: int
    avg_savings_pct: float
    total_exec_time_ms: int


@dataclass(frozen=True, slots=True)
class RtkDailyStats:
    date: str
    commands: int
    saved_tokens: int
    savings_pct: float


class RtkReader:
    """Read-only reader for RTK's SQLite tracking database."""

    def __init__(self, db_path: str | Path = "") -> None:
        self._path = Path(db_path) if db_path else self.find_db()
        self._conn: sqlite3.Connection | None = None

    @staticmethod
    def find_db() -> Path:
        system = platform.system()
        if system == "Darwin":
            return Path.home() / "Library" / "Application Support" / "rtk" / "tracking.db"
        if system == "Windows":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                return Path(appdata) / "rtk" / "tracking.db"
        xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        return Path(xdg) / "rtk" / "tracking.db"

    @property
    def available(self) -> bool:
        return self._path.is_file()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self._path.is_file():
                raise FileNotFoundError(f"RTK tracking DB not found: {self._path}")
            self._conn = sqlite3.connect(
                f"file:{self._path}?mode=ro", uri=True, check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def get_summary(
        self,
        project_path: str | None = None,
        since: str | None = None,
    ) -> RtkSummary:
        conn = self._get_conn()
        where_parts: list[str] = []
        params: list[str] = []

        if project_path:
            where_parts.append("(project_path = ? OR project_path GLOB ?)")
            params.extend([project_path, f"{project_path}{os.sep}*"])
        if since:
            where_parts.append("timestamp >= ?")
            params.append(since)

        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        row = conn.execute(
            f"""SELECT
                    COUNT(*) as total_commands,
                    COALESCE(SUM(input_tokens), 0) as total_input,
                    COALESCE(SUM(output_tokens), 0) as total_output,
                    COALESCE(SUM(saved_tokens), 0) as total_saved,
                    COALESCE(AVG(savings_pct), 0.0) as avg_pct,
                    COALESCE(SUM(exec_time_ms), 0) as total_time
                FROM commands {where_clause}""",
            params,
        ).fetchone()

        return RtkSummary(
            total_commands=row["total_commands"],
            total_input_tokens=row["total_input"],
            total_output_tokens=row["total_output"],
            total_saved_tokens=row["total_saved"],
            avg_savings_pct=round(row["avg_pct"], 2),
            total_exec_time_ms=row["total_time"],
        )

    def get_daily_savings(
        self,
        days: int = 30,
        project_path: str | None = None,
    ) -> list[RtkDailyStats]:
        conn = self._get_conn()
        where_parts = [f"timestamp >= date('now', '-{days} days')"]
        params: list[str] = []

        if project_path:
            where_parts.append("(project_path = ? OR project_path GLOB ?)")
            params.extend([project_path, f"{project_path}{os.sep}*"])

        where_clause = " AND ".join(where_parts)

        rows = conn.execute(
            f"""SELECT
                    date(timestamp) as day,
                    COUNT(*) as commands,
                    SUM(saved_tokens) as saved,
                    AVG(savings_pct) as avg_pct
                FROM commands
                WHERE {where_clause}
                GROUP BY day
                ORDER BY day""",
            params,
        ).fetchall()

        return [
            RtkDailyStats(
                date=row["day"],
                commands=row["commands"],
                saved_tokens=row["saved"],
                savings_pct=round(row["avg_pct"], 2),
            )
            for row in rows
        ]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
