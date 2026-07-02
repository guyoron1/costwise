"""Auto-configure Claude Code to use Costwise MCP, proxy, and Ponytail."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click

from costwise.config.loader import load_config

_SUPPORTED_TARGETS = {"claude"}

_PONYTAIL_REPO = "https://github.com/DietrichGebert/ponytail.git"
_PONYTAIL_DIR = Path.home() / ".costwise" / "ponytail"


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


def _clone_ponytail() -> Path | None:
    """Clone Ponytail repo to ~/.costwise/ponytail if not present."""
    if _PONYTAIL_DIR.exists():
        return _PONYTAIL_DIR

    if not shutil.which("git"):
        return None

    try:
        _PONYTAIL_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", _PONYTAIL_REPO, str(_PONYTAIL_DIR)],
            capture_output=True,
            timeout=30,
            check=True,
        )
        return _PONYTAIL_DIR
    except (subprocess.SubprocessError, OSError):
        return None


def _build_ponytail_hooks(ponytail_dir: Path) -> dict:
    """Build Claude Code hooks config pointing at the cloned Ponytail hooks."""
    hooks_dir = ponytail_dir / "hooks"
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [
                    {
                        "type": "command",
                        "command": f'node "{hooks_dir / "ponytail-activate.js"}"; exit 0',
                        "timeout": 5,
                    }
                ],
            }
        ],
        "SubagentStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'node "{hooks_dir / "ponytail-subagent.js"}"; exit 0',
                        "timeout": 5,
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'node "{hooks_dir / "ponytail-mode-tracker.js"}"; exit 0',
                        "timeout": 5,
                    }
                ],
            }
        ],
    }


def _apply_changes(
    settings: dict,
    *,
    add_mcp: bool,
    add_proxy: bool,
    proxy_url: str,
    add_ponytail: bool,
    ponytail_dir: Path | None,
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

    if add_ponytail and ponytail_dir:
        ponytail_hooks = _build_ponytail_hooks(ponytail_dir)
        existing_hooks = updated.get("hooks", {})
        needs_update = False
        for event, entries in ponytail_hooks.items():
            if event not in existing_hooks:
                needs_update = True
                break
            existing_cmds = [
                h.get("command", "")
                for group in existing_hooks[event]
                for h in group.get("hooks", [])
            ]
            if not any("ponytail" in cmd for cmd in existing_cmds):
                needs_update = True
                break

        if needs_update:
            if "hooks" not in updated:
                updated["hooks"] = {}
            for event, entries in ponytail_hooks.items():
                if event not in updated["hooks"]:
                    updated["hooks"][event] = []
                updated["hooks"][event].extend(entries)
            changes.append(f"  + hooks.ponytail → {ponytail_dir / 'hooks'}")
        else:
            changes.append("  = hooks.ponytail (already configured)")

    return updated, changes


@click.command("wrap")
@click.argument("target", default="claude")
@click.option("--dry-run", is_flag=True, help="Show changes without writing")
@click.option("--proxy/--no-proxy", default=True, help="Configure proxy URL")
@click.option("--mcp/--no-mcp", "add_mcp", default=True, help="Configure MCP server")
@click.option(
    "--ponytail/--no-ponytail", default=True,
    help="Install and configure Ponytail plugin",
)
def wrap(target: str, dry_run: bool, proxy: bool, add_mcp: bool, ponytail: bool) -> None:
    """Auto-configure an AI coding tool to use Costwise.

    Currently supports: claude (Claude Code)

    Installs the full optimization stack: proxy routing, MCP tools,
    and Ponytail (lazy senior dev mode for output reduction).
    """
    if target not in _SUPPORTED_TARGETS:
        click.echo(f"Unsupported target: {target}")
        click.echo(f"Supported: {', '.join(sorted(_SUPPORTED_TARGETS))}")
        raise SystemExit(1)

    config = load_config()
    proxy_url = f"http://{config.proxy.host}:{config.proxy.port}"

    ponytail_dir: Path | None = None
    if ponytail:
        if _PONYTAIL_DIR.exists():
            ponytail_dir = _PONYTAIL_DIR
            click.echo(f"Ponytail: found at {_PONYTAIL_DIR}")
        elif not dry_run:
            click.echo("Ponytail: cloning DietrichGebert/ponytail...")
            ponytail_dir = _clone_ponytail()
            if ponytail_dir:
                click.echo(f"Ponytail: installed to {ponytail_dir}")
            else:
                click.echo("Ponytail: clone failed (git not found or network error)")
                click.echo("  Manual: /plugin marketplace add DietrichGebert/ponytail")
        else:
            ponytail_dir = _PONYTAIL_DIR
            click.echo(f"Ponytail: would clone to {_PONYTAIL_DIR}")

    settings_path = _find_claude_settings()
    settings = _read_settings(settings_path)

    updated, changes = _apply_changes(
        settings,
        add_mcp=add_mcp,
        add_proxy=proxy,
        proxy_url=proxy_url,
        add_ponytail=ponytail,
        ponytail_dir=ponytail_dir,
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
