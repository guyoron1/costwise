"""Auto-configure Claude Code to use Costwise MCP and proxy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from costwise.config.loader import load_config

_SUPPORTED_TARGETS = {"claude"}


def _find_claude_settings() -> Path:
    claude_dir = Path.home() / ".claude"
    project_settings = claude_dir / "settings.local.json"
    if project_settings.exists():
        return project_settings
    return claude_dir / "settings.json"


def _read_settings(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _build_mcp_entry() -> dict:
    return {
        "command": sys.executable,
        "args": ["-m", "costwise.mcp"],
    }


def _apply_changes(
    settings: dict,
    *,
    add_mcp: bool,
    add_proxy: bool,
    proxy_url: str,
) -> tuple[dict, list[str]]:
    updated = json.loads(json.dumps(settings))
    changes: list[str] = []

    if add_mcp:
        if "mcpServers" not in updated:
            updated["mcpServers"] = {}
        existing = updated["mcpServers"].get("costwise")
        new_entry = _build_mcp_entry()
        if existing != new_entry:
            updated["mcpServers"]["costwise"] = new_entry
            changes.append(f"  + mcpServers.costwise → {new_entry['command']} -m costwise.mcp")
        else:
            changes.append("  = mcpServers.costwise (already configured)")

    if add_proxy:
        if "env" not in updated:
            updated["env"] = {}
        old_url = updated["env"].get("ANTHROPIC_BASE_URL")
        if old_url != proxy_url:
            updated["env"]["ANTHROPIC_BASE_URL"] = proxy_url
            changes.append(f"  + env.ANTHROPIC_BASE_URL → {proxy_url}")
        else:
            changes.append(f"  = env.ANTHROPIC_BASE_URL (already {proxy_url})")

    return updated, changes


@click.command("wrap")
@click.argument("target", default="claude")
@click.option("--dry-run", is_flag=True, help="Show changes without writing")
@click.option("--proxy/--no-proxy", default=True, help="Configure proxy URL")
@click.option("--mcp/--no-mcp", "add_mcp", default=True, help="Configure MCP server")
def wrap(target: str, dry_run: bool, proxy: bool, add_mcp: bool) -> None:
    """Auto-configure an AI coding tool to use Costwise.

    Currently supports: claude (Claude Code)
    """
    if target not in _SUPPORTED_TARGETS:
        click.echo(f"Unsupported target: {target}")
        click.echo(f"Supported: {', '.join(sorted(_SUPPORTED_TARGETS))}")
        raise SystemExit(1)

    config = load_config()
    proxy_url = f"http://{config.proxy.host}:{config.proxy.port}"

    settings_path = _find_claude_settings()
    settings = _read_settings(settings_path)

    updated, changes = _apply_changes(
        settings,
        add_mcp=add_mcp,
        add_proxy=proxy,
        proxy_url=proxy_url,
    )

    if not changes:
        click.echo("No changes needed.")
        return

    click.echo(f"Target: {target}")
    click.echo(f"Config: {settings_path}")
    click.echo("Changes:")
    for change in changes:
        click.echo(change)

    if dry_run:
        click.echo("\n(dry run — no files modified)")
        return

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(updated, indent=2) + "\n",
        encoding="utf-8",
    )
    click.echo(f"\nWritten to {settings_path}")
