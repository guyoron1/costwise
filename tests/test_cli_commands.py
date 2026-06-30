"""Tests for CLI commands: doctor, wrap, dashboard, mcp."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from costwise.cli.doctor_cmd import doctor, _check_config, CheckResult
from costwise.cli.wrap_cmd import wrap, _apply_changes, _build_mcp_entry
from costwise.cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestDoctor:
    def test_runs_all_checks(self, runner: CliRunner) -> None:
        result = runner.invoke(doctor)
        assert result.exit_code == 0
        assert "Costwise Doctor" in result.output

    def test_json_output(self, runner: CliRunner) -> None:
        result = runner.invoke(doctor, ["--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert all("name" in item and "passed" in item for item in data)

    def test_check_config_works(self) -> None:
        result = _check_config()
        assert isinstance(result, CheckResult)
        assert result.name == "Config"

    def test_doctor_registered_in_cli(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["doctor", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)


class TestWrap:
    def test_dry_run(self, runner: CliRunner, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text("{}")

        with patch("costwise.cli.wrap_cmd._find_claude_settings", return_value=settings):
            result = runner.invoke(wrap, ["claude", "--dry-run"])
            assert result.exit_code == 0
            assert "dry run" in result.output

    def test_writes_mcp_config(self, runner: CliRunner, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text("{}")

        with patch("costwise.cli.wrap_cmd._find_claude_settings", return_value=settings):
            result = runner.invoke(wrap, ["claude"])
            assert result.exit_code == 0

            written = json.loads(settings.read_text())
            assert "mcpServers" in written
            assert "costwise" in written["mcpServers"]

    def test_idempotent(self, runner: CliRunner, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text("{}")

        with patch("costwise.cli.wrap_cmd._find_claude_settings", return_value=settings):
            runner.invoke(wrap, ["claude"])
            first = json.loads(settings.read_text())
            runner.invoke(wrap, ["claude"])
            second = json.loads(settings.read_text())
            assert first == second

    def test_preserves_existing_config(self, runner: CliRunner, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text(json.dumps({"existing_key": "value", "mcpServers": {"other": {}}}))

        with patch("costwise.cli.wrap_cmd._find_claude_settings", return_value=settings):
            runner.invoke(wrap, ["claude"])
            written = json.loads(settings.read_text())
            assert written["existing_key"] == "value"
            assert "other" in written["mcpServers"]
            assert "costwise" in written["mcpServers"]

    def test_unsupported_target(self, runner: CliRunner) -> None:
        result = runner.invoke(wrap, ["cursor"])
        assert result.exit_code != 0
        assert "Unsupported" in result.output

    def test_apply_changes_mcp(self) -> None:
        updated, changes = _apply_changes({}, add_mcp=True, add_proxy=False, proxy_url="")
        assert "mcpServers" in updated
        assert "costwise" in updated["mcpServers"]
        assert len(changes) == 1

    def test_apply_changes_proxy(self) -> None:
        updated, changes = _apply_changes(
            {}, add_mcp=False, add_proxy=True, proxy_url="http://127.0.0.1:8788"
        )
        assert updated["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"

    def test_build_mcp_entry(self) -> None:
        entry = _build_mcp_entry()
        assert "command" in entry
        assert "args" in entry
        assert "-m" in entry["args"]
        assert "costwise.mcp" in entry["args"]


class TestCLIRegistration:
    def test_dashboard_command_exists(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output.lower()

    def test_mcp_command_exists(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0

    def test_wrap_command_exists(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["wrap", "--help"])
        assert result.exit_code == 0
        assert "claude" in result.output.lower()

    def test_gain_still_works(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["gain"])
        assert result.exit_code == 0
        assert "Costwise Gain" in result.output
