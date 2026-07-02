"""Token usage summary from SQLite — the 'costwise gain' command."""

from __future__ import annotations

import asyncio

import click

from costwise.config.loader import load_config
from costwise.integrations.ponytail import _OUTPUT_SAVINGS_BY_MODE
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


def _format_ponytail_line(ponytail_stats: list[dict]) -> str | None:
    if not ponytail_stats:
        return None
    total = sum(row.get("count", 0) for row in ponytail_stats)
    if total == 0:
        return None
    top_mode = ponytail_stats[0].get("ponytail_mode", "full")
    ratio = _OUTPUT_SAVINGS_BY_MODE.get(top_mode, 0.0)
    pct = int(ratio * 100)
    return f"  Ponytail:  {total:,} reqs @ {top_mode} (est. ~{pct}% output reduction)"


def _format_summary(stats: dict, ponytail_stats: list[dict] | None = None) -> str:
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

    ponytail_line = _format_ponytail_line(ponytail_stats or [])
    if ponytail_line:
        lines.append(ponytail_line)

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

    async def _run() -> tuple[dict, list[dict]]:
        await store.initialize()
        if session:
            return {"session_stats": await store.get_session_stats(session)}, []
        summary = await store.get_gain_summary()
        ponytail = await store.get_ponytail_summary()
        return summary, ponytail

    stats, ponytail_stats = asyncio.run(_run())
    store.close()

    if as_json:
        import json
        stats["ponytail"] = ponytail_stats
        click.echo(json.dumps(stats, indent=2, default=str))
    else:
        click.echo(_format_summary(stats, ponytail_stats))
