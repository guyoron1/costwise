"""Guided installer for Costwise and its companion tools."""

from __future__ import annotations

import shutil
import subprocess
import sys

import click


def _external_install(name: str, argv: list[str]) -> bool:
    """Install an external tool via a system command."""
    click.echo(f"  Running: {' '.join(argv)}")
    result = subprocess.run(argv, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        click.echo(f"  ✓ {name} installed")
        return True
    click.echo(f"  ✗ Failed (exit {result.returncode})")
    if result.stderr:
        for line in result.stderr.strip().splitlines()[:3]:
            click.echo(f"    {line}")
    return False


def _check_graphify() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("graphify") is not None
    except Exception:
        return False


def _check_headroom() -> bool:
    try:
        from costwise.integrations.headroom import is_available

        return is_available()
    except Exception:
        return False


def _check_ponytail() -> bool:
    from pathlib import Path

    if (Path.home() / ".config" / "ponytail" / "config.json").exists():
        return True
    if shutil.which("npm"):
        result = subprocess.run(
            ["npm", "list", "-g", "@dietrichgebert/ponytail"],
            check=False, capture_output=True, text=True,
        )
        return result.returncode == 0
    return False


@click.command("setup")
def setup() -> None:
    """Install all Costwise companion tools in one shot."""
    click.echo("Costwise Setup\n")

    graphify_ok = _check_graphify()
    headroom_ok = _check_headroom()
    rtk_ok = shutil.which("rtk") is not None
    ponytail_ok = _check_ponytail()

    if graphify_ok and headroom_ok and rtk_ok and ponytail_ok:
        click.echo("  ✓ All companion tools already installed.")
        return

    installed = 0
    failed = 0

    # --- Python extras (single pip install) ---
    if not graphify_ok or not headroom_ok:
        click.echo("  Installing Python extras (Graphify + Headroom)...")
        argv = [sys.executable, "-m", "pip", "install", "costwise[all]", "-q"]
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("  ✓ Python extras installed")
            installed += 1
        else:
            click.echo(f"  ✗ pip install failed (exit {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().splitlines()[:3]:
                    click.echo(f"    {line}")
            failed += 1
    else:
        click.echo("  ✓ Graphify — already installed")
        click.echo("  ✓ Headroom — already installed")

    # --- RTK ---
    if not rtk_ok:
        if shutil.which("brew"):
            click.echo("  Installing RTK via Homebrew...")
            if _external_install("RTK", ["brew", "install", "rtk"]):
                installed += 1
            else:
                failed += 1
        else:
            click.echo("  ✗ RTK — brew not found, install manually: brew install rtk")
            failed += 1
    else:
        click.echo("  ✓ RTK — already installed")

    # --- Ponytail ---
    if not ponytail_ok:
        if shutil.which("npm"):
            click.echo("  Installing Ponytail via npm...")
            if _external_install("Ponytail", ["npm", "install", "-g", "@dietrichgebert/ponytail"]):
                installed += 1
            else:
                failed += 1
        else:
            click.echo(
                "  ✗ Ponytail — npm not found, install manually:"
                " npm install -g @dietrichgebert/ponytail"
            )
            failed += 1
    else:
        click.echo("  ✓ Ponytail — already installed")

    # --- Summary ---
    click.echo(f"\nDone: {installed} installed, {failed} failed")
    if failed == 0:
        click.echo("All tools ready. Run 'costwise doctor' to verify.")
