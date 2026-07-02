"""Health checks for all Costwise integration points."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click

INSTALL_HINTS: dict[str, list[str]] = {
    "Graph": ['pip install "costwise[graph]"'],
    "RTK": [
        "brew install rtk",
    ],
    "Ponytail": ["npm install -g @dietrichgebert/ponytail"],
    "Headroom": ['pip install "costwise[headroom]"'],
    "Vertex AI": ['pip install "costwise[vertex]"'],
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    install_hint: list[str] = field(default_factory=list)


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


def _check_graphify() -> CheckResult:
    try:
        import importlib.util

        if importlib.util.find_spec("graphify") is not None:
            return CheckResult("Graphify", True, "installed (python package)")
        return CheckResult(
            "Graphify", False, "not installed",
            install_hint=INSTALL_HINTS["Graph"],
        )
    except Exception:
        return CheckResult(
            "Graphify", False, "not installed",
            install_hint=INSTALL_HINTS["Graph"],
        )


def _check_graph() -> CheckResult:
    try:
        from costwise.config.loader import load_config

        config = load_config()
        graph_path = Path(config.graph.graph_path)
        if graph_path.exists():
            from costwise.graph.loader import load_graph

            g = load_graph(graph_path)
            return CheckResult("Graph Data", True, f"{len(g.nodes)} nodes at {graph_path}")
        return CheckResult("Graph Data", False, f"not found at {graph_path}")
    except Exception as e:
        return CheckResult("Graph Data", False, str(e))


def _check_rtk() -> CheckResult:
    hints = INSTALL_HINTS["RTK"]
    try:
        if shutil.which("rtk") is None:
            return CheckResult("RTK", False, "binary not found on PATH", install_hint=hints)
        from costwise.integrations.rtk import RtkReader

        reader = RtkReader()
        if reader.available:
            summary = reader.get_summary()
            reader.close()
            return CheckResult(
                "RTK", True,
                f"{summary.total_commands} commands tracked"
            )
        return CheckResult("RTK", False, "tracking.db not found (run rtk init first)")
    except Exception:
        return CheckResult("RTK", False, "not installed", install_hint=hints)


def _check_ponytail() -> CheckResult:
    hints = INSTALL_HINTS["Ponytail"]
    try:
        from costwise.integrations.ponytail import PonytailReader, _read_runtime_mode

        reader = PonytailReader()
        mode = reader.get_mode()
        runtime = _read_runtime_mode()
        parts = []
        if runtime:
            parts.append(f"live: {runtime}")
        if mode and not runtime:
            parts.append(f"default: {mode}")
        plugin_dir = Path.home() / ".costwise" / "ponytail"
        if plugin_dir.exists():
            parts.append("installed via costwise")
        if parts:
            return CheckResult("Ponytail", True, ", ".join(parts))
        return CheckResult("Ponytail", False, "not detected", install_hint=hints)
    except Exception:
        return CheckResult("Ponytail", False, "not detected", install_hint=hints)


def _check_headroom() -> CheckResult:
    hints = INSTALL_HINTS["Headroom"]
    try:
        from costwise.integrations.headroom import is_available

        if is_available():
            return CheckResult("Headroom", True, "importable")
        return CheckResult("Headroom", False, "not installed", install_hint=hints)
    except Exception:
        return CheckResult("Headroom", False, "not installed", install_hint=hints)


def _check_vertex() -> CheckResult:
    try:
        from costwise.config.loader import load_config

        config = load_config()
        if not config.proxy.vertex.enabled:
            return CheckResult("Vertex AI", True, "not enabled")
        try:
            from costwise.proxy.vertex import VertexAuthProvider

            auth = VertexAuthProvider()
            auth.get_token()
            return CheckResult(
                "Vertex AI", True,
                f"project={config.proxy.vertex.project_id} region={config.proxy.vertex.region}",
            )
        except ImportError:
            return CheckResult(
                "Vertex AI", False,
                "google-auth not installed",
                install_hint=INSTALL_HINTS["Vertex AI"],
            )
        except Exception as e:
            return CheckResult("Vertex AI", False, f"auth failed: {e}")
    except Exception as e:
        return CheckResult("Vertex AI", False, str(e))


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
    _check_graphify,
    _check_graph,
    _check_rtk,
    _check_ponytail,
    _check_headroom,
    _check_vertex,
    _check_claude_config,
]


def _format_results(results: list[CheckResult], *, show_hints: bool = True) -> str:
    lines = []
    hints: list[str] = []
    for r in results:
        icon = "✓" if r.passed else "✗"
        detail = f" ({r.detail})" if r.detail else ""
        lines.append(f"  {icon} {r.name}{detail}")
        if not r.passed and r.install_hint and show_hints:
            for cmd in r.install_hint:
                hints.append(f"    → {cmd}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines.append(f"  {passed}/{total} checks passed")

    width = max(len(line) for line in lines + hints) + 2
    top = f"╭─ Costwise Doctor {'─' * (width - 17)}╮"
    bot = f"╰{'─' * (width + 1)}╯"
    body = "\n".join(f"│{line.ljust(width)}│" for line in lines)
    box = f"{top}\n{body}\n{bot}"

    if hints:
        box += "\n\n  To install missing tools:\n" + "\n".join(hints)
        box += '\n\n  Or install all Python extras at once: pip install "costwise[all]"'

    return box


def _run_install(cmd: str) -> bool:
    """Run an install command, returning True on success."""
    click.echo(f"  Running: {cmd}")
    try:
        argv = shlex.split(cmd)
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("  ✓ Success")
            return True
        click.echo(f"  ✗ Failed (exit {result.returncode})")
        if result.stderr:
            for line in result.stderr.strip().splitlines()[:3]:
                click.echo(f"    {line}")
        return False
    except Exception as e:
        click.echo(f"  ✗ Error: {e}")
        return False


@click.command("doctor")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.option("--install", "do_install", is_flag=True, help="Offer to install missing tools")
def doctor(as_json: bool, do_install: bool) -> None:
    """Health checks for all Costwise integration points."""
    results = [check() for check in _ALL_CHECKS]

    if as_json:
        click.echo(json.dumps([asdict(r) for r in results], indent=2))
        return

    click.echo(_format_results(results))

    if not do_install:
        return

    failed = [r for r in results if not r.passed and r.install_hint]
    if not failed:
        click.echo("\n  All installable tools are present.")
        return

    click.echo(f"\n  {len(failed)} tool(s) can be installed:")
    for r in failed:
        click.echo(f"    • {r.name}: {r.install_hint[0]}")

    if not click.confirm("\n  Install missing tools?", default=True):
        return

    for r in failed:
        click.echo(f"\n  Installing {r.name}...")
        _run_install(r.install_hint[0])
