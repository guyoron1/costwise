"""Token usage summary from SQLite — the 'costwise gain' command."""

from __future__ import annotations

import asyncio

import click

from costwise.config.loader import load_config
from costwise.tracking.store import TrackingStore


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n:,.0f}"
    return str(n)


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    return iso[:10]


def _format_summary(stats: dict) -> str:
    reqs = stats.get("total_requests") or 0
    if reqs == 0:
        return "╭─ Costwise Gain ─────────────────────╮\n│  No requests tracked yet.           │\n╰─────────────────────────────────────╯"

    prompt = stats.get("total_prompt_tokens")
    comp = stats.get("total_completion_tokens")
    cost = stats.get("total_cost_usd")
    saved = stats.get("total_saved_usd")
    first = _fmt_date(stats.get("first_request"))
    last = _fmt_date(stats.get("last_request"))

    lines = [
        f"  Requests:  {reqs:,}",
        f"  Tokens:    {_fmt_tokens(prompt)} in / {_fmt_tokens(comp)} out",
    ]

    if cost is not None:
        lines.append(f"  Cost:      ${cost:,.2f}")
    if saved is not None and cost is not None:
        pct = (saved / (cost + saved) * 100) if (cost + saved) > 0 else 0
        lines.append(f"  Saved:     ${saved:,.2f} ({pct:.1f}%)")
    elif saved is not None:
        lines.append(f"  Saved:     ${saved:,.2f}")

    period = f"{first} – {last}" if first != last else first
    lines.append(f"  Period:    {period}")

    width = max(len(line) for line in lines) + 2
    top = f"╭─ Costwise Gain {'─' * (width - 16)}╮"
    bot = f"╰{'─' * (width + 1)}╯"
    body = "\n".join(f"│{line.ljust(width)}│" for line in lines)
    return f"{top}\n{body}\n{bot}"


@click.command("gain")
@click.option("--session", default=None, help="Filter by session ID")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def gain(session: str | None, as_json: bool) -> None:
    """Show token usage and cost savings summary."""
    config = load_config()
    store = TrackingStore(config.tracking.db_path)

    async def _run() -> dict:
        await store.initialize()
        if session:
            return {"session_stats": await store.get_session_stats(session)}
        return await store.get_gain_summary()

    stats = asyncio.run(_run())
    store.close()

    if as_json:
        import json
        click.echo(json.dumps(stats, indent=2, default=str))
    else:
        click.echo(_format_summary(stats))
