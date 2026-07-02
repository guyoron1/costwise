"""Tests for RTK integration — read-only SQLite reader."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from costwise.integrations.rtk import RtkReader


def _create_rtk_db(path):
    """Create a minimal RTK tracking database for testing."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE commands (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            original_cmd TEXT NOT NULL,
            rtk_cmd TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            saved_tokens INTEGER NOT NULL,
            savings_pct REAL NOT NULL,
            exec_time_ms INTEGER DEFAULT 0,
            project_path TEXT DEFAULT ''
        )"""
    )
    conn.execute("CREATE INDEX idx_timestamp ON commands(timestamp)")
    conn.commit()
    return conn


def _insert_command(conn, input_tokens=1000, output_tokens=300, project_path="", days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    saved = input_tokens - output_tokens
    pct = (saved / input_tokens * 100) if input_tokens > 0 else 0.0
    conn.execute(
        """INSERT INTO commands
           (timestamp, original_cmd, rtk_cmd, input_tokens, output_tokens,
            saved_tokens, savings_pct, exec_time_ms, project_path)
           VALUES (?, 'ls -la', 'rtk ls', ?, ?, ?, ?, 50, ?)""",
        (ts, input_tokens, output_tokens, saved, pct, project_path),
    )
    conn.commit()


@pytest.fixture()
def rtk_db(tmp_path):
    db_path = tmp_path / "tracking.db"
    conn = _create_rtk_db(db_path)
    _insert_command(conn, 1000, 300, "/project/a", 0)
    _insert_command(conn, 2000, 500, "/project/a", 1)
    _insert_command(conn, 500, 200, "/project/b", 0)
    conn.close()
    return db_path


class TestRtkReader:
    def test_available_when_exists(self, rtk_db):
        reader = RtkReader(rtk_db)
        assert reader.available is True

    def test_not_available_when_missing(self, tmp_path):
        reader = RtkReader(tmp_path / "nope.db")
        assert reader.available is False

    def test_get_summary_all(self, rtk_db):
        reader = RtkReader(rtk_db)
        summary = reader.get_summary()
        assert summary.total_commands == 3
        assert summary.total_input_tokens == 3500
        assert summary.total_output_tokens == 1000
        assert summary.total_saved_tokens == 2500
        assert summary.avg_savings_pct > 0
        reader.close()

    def test_get_summary_by_project(self, rtk_db):
        reader = RtkReader(rtk_db)
        summary = reader.get_summary(project_path="/project/a")
        assert summary.total_commands == 2
        assert summary.total_input_tokens == 3000
        reader.close()

    def test_get_summary_empty_project(self, rtk_db):
        reader = RtkReader(rtk_db)
        summary = reader.get_summary(project_path="/nonexistent")
        assert summary.total_commands == 0
        assert summary.total_saved_tokens == 0
        reader.close()

    def test_get_daily_savings(self, rtk_db):
        reader = RtkReader(rtk_db)
        daily = reader.get_daily_savings(days=7)
        assert len(daily) >= 1
        assert all(d.saved_tokens > 0 for d in daily)
        reader.close()

    def test_get_daily_savings_by_project(self, rtk_db):
        reader = RtkReader(rtk_db)
        daily = reader.get_daily_savings(days=7, project_path="/project/b")
        total_saved = sum(d.saved_tokens for d in daily)
        assert total_saved == 300
        reader.close()

    def test_missing_db_raises(self, tmp_path):
        reader = RtkReader(tmp_path / "nope.db")
        with pytest.raises(FileNotFoundError):
            reader.get_summary()

    def test_find_db_returns_path(self):
        path = RtkReader.find_db()
        assert "rtk" in str(path)
        assert str(path).endswith("tracking.db")

    def test_close_idempotent(self, rtk_db):
        reader = RtkReader(rtk_db)
        reader.get_summary()
        reader.close()
        reader.close()
