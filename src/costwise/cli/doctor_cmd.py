"""Health checks for all Costwise integration points."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import click


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _check_config() -> CheckResult:
    try:
        from costwise.config.loader import find_config_file, load_config

        path = find_config_file()
        load_config(path)
        detail = str(path) if path else "defaults (no config file)"
        return CheckResult("Config", True, detail)
    except Exception as e:
        return CheckResult("Config", False, str(e))


def _check_db() -> CheckResult:
    try:
        from costwise.config.loader import load_config

        config = load_config()
        db_path = config.tracking.db_path
        if db_path.exists():
            import sqlite3

            conn = sqlite3.connect(str(db_path), timeout=2)
            row = conn.execute("SELECT COUNT(*) FROM routing_decisions").fetchone()
            conn.close()
            return CheckResult("Tracking DB", True, f"{row[0]} records at {db_path}")
        return CheckResult("Tracking DB", False, f"not found at {db_path}")
    except Exception as e:
        return CheckResult("Tracking DB", False, str(e))


def _check_proxy() -> CheckResult:
    try:
        from costwise.config.loader import load_config

        config = load_config()
        url = f"http://{config.proxy.host}:{config.proxy.port}/health"

        import httpx

        resp = httpx.get(url, timeout=2)
        if resp.status_code == 200:
            return CheckResult("Proxy", True, f"running at {url}")
        return CheckResult("Proxy", False, f"status {resp.status_code}")
    except Exception:
        return CheckResult("Proxy", False, "not running")


def _check_dashboard() -> CheckResult:
    try:
        import httpx

        resp = httpx.get("http://127.0.0.1:8789/health", timeout=2)
        if resp.status_code == 200:
            return CheckResult("Dashboard", True, "running at :8789")
        return CheckResult("Dashboard", False, f"status {resp.status_code}")
    except Exception:
        return CheckResult("Dashboard", False, "not running")


def _check_graph() -> CheckResult:
    try:
        from costwise.config.loader import load_config

        config = load_config()
        graph_path = Path(config.graph.graph_path)
        if graph_path.exists():
            from costwise.graph.loader import load_graph

            g = load_graph(graph_path)
            return CheckResult("Graph", True, f"{len(g.nodes)} nodes at {graph_path}")
        return CheckResult("Graph", False, f"not found at {graph_path}")
    except Exception as e:
        return CheckResult("Graph", False, str(e))


def _check_rtk() -> CheckResult:
    try:
        from costwise.integrations.rtk import RtkReader

        reader = RtkReader()
        if reader.available:
            summary = reader.get_summary()
            reader.close()
            return CheckResult(
                "RTK", True,
                f"{summary.total_commands} commands tracked"
            )
        return CheckResult("RTK", False, "tracking.db not found")
    except Exception:
        return CheckResult("RTK", False, "not installed")


def _check_ponytail() -> CheckResult:
    try:
        from costwise.integrations.ponytail import PonytailReader

        reader = PonytailReader()
        mode = reader.get_mode()
        if mode:
            return CheckResult("Ponytail", True, f"mode: {mode}")
        return CheckResult("Ponytail", False, "config not found")
    except Exception:
        return CheckResult("Ponytail", False, "not detected")


def _check_headroom() -> CheckResult:
    try:
        from costwise.integrations.headroom import is_available

        if is_available():
            return CheckResult("Headroom", True, "importable")
        return CheckResult("Headroom", False, "not installed")
    except Exception:
        return CheckResult("Headroom", False, "not installed")


def _check_claude_config() -> CheckResult:
    claude_dir = Path.home() / ".claude"
    if not claude_dir.exists():
        return CheckResult("Claude Code", False, "~/.claude/ not found")

    for filename in ["settings.json", "settings.local.json"]:
        settings_path = claude_dir / filename
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
                mcp_servers = data.get("mcpServers", {})
                if "costwise" in mcp_servers:
                    return CheckResult("Claude Code", True, f"MCP configured in {filename}")
            except Exception:
                continue

    return CheckResult("Claude Code", False, "MCP not configured")


_ALL_CHECKS = [
    _check_config,
    _check_db,
    _check_proxy,
    _check_dashboard,
    _check_graph,
    _check_rtk,
    _check_ponytail,
    _check_headroom,
    _check_claude_config,
]


def _format_results(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        icon = "✓" if r.passed else "✗"
        detail = f" ({r.detail})" if r.detail else ""
        lines.append(f"  {icon} {r.name}{detail}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines.append(f"  {passed}/{total} checks passed")

    width = max(len(line) for line in lines) + 2
    top = f"╭─ Costwise Doctor {'─' * (width - 17)}╮"
    bot = f"╰{'─' * (width + 1)}╯"
    body = "\n".join(f"│{line.ljust(width)}│" for line in lines)
    return f"{top}\n{body}\n{bot}"


@click.command("doctor")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def doctor(as_json: bool) -> None:
    """Health checks for all Costwise integration points."""
    results = [check() for check in _ALL_CHECKS]

    if as_json:
        click.echo(json.dumps([asdict(r) for r in results], indent=2))
    else:
        click.echo(_format_results(results))
