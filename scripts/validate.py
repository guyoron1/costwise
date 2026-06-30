#!/usr/bin/env python3
"""Costwise validation harness — replay recorded routing decisions to prove savings.

Reads from the SQLite tracking database and computes cost comparisons:
  1. Baseline: what if every request went to the most expensive model?
  2. Routing only: actual routed costs vs baseline
  3. Routing + pruning: measured token savings from context pruning
  4. Full stack estimate: projected savings with RTK + Ponytail + Headroom

Usage:
    python scripts/validate.py
    python scripts/validate.py --db ~/.local/share/costwise/costwise.db
    python scripts/validate.py --json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from costwise.config.loader import load_config
from costwise.core.pricing import MODELS, PricingRegistry


@dataclass
class LayerResult:
    label: str
    cost_usd: float
    saved_usd: float
    savings_pct: float


def _find_db(explicit_path: str | None = None) -> Path:
    if explicit_path:
        p = Path(explicit_path)
        if not p.exists():
            print(f"Error: database not found at {p}", file=sys.stderr)
            sys.exit(1)
        return p

    config = load_config()
    db_path = config.tracking.db_path
    if db_path.exists():
        return db_path

    print(f"Error: no tracking database found at {db_path}", file=sys.stderr)
    print("Run the proxy first to collect data, then re-run this script.", file=sys.stderr)
    sys.exit(1)


def _load_decisions(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    columns = {row[1] for row in conn.execute("PRAGMA table_info(routing_decisions)")}
    has_pruning = "tokens_pruned" in columns

    if has_pruning:
        query = """SELECT session_id, request_model, routed_model, tier,
                          prompt_tokens, completion_tokens, total_tokens,
                          cost_usd, saved_usd, tokens_pruned, messages_pruned
                   FROM routing_decisions ORDER BY id"""
    else:
        query = """SELECT session_id, request_model, routed_model, tier,
                          prompt_tokens, completion_tokens, total_tokens,
                          cost_usd, saved_usd, 0 as tokens_pruned, 0 as messages_pruned
                   FROM routing_decisions ORDER BY id"""

    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _compute_layers(
    decisions: list[dict],
    registry: PricingRegistry,
) -> list[LayerResult]:
    baseline_model = max(MODELS, key=lambda m: m.blended_cost_per_mtok)

    baseline_total = 0.0
    routed_total = 0.0
    pruned_tokens_total = 0
    pruned_messages_total = 0

    for d in decisions:
        prompt = d["prompt_tokens"] or 0
        completion = d["completion_tokens"] or 0

        baseline_cost = registry.estimate_cost(baseline_model.name, prompt, completion) or 0.0
        baseline_total += baseline_cost

        actual_cost = d["cost_usd"] or 0.0
        routed_total += actual_cost

        pruned_tokens_total += d["tokens_pruned"] or 0
        pruned_messages_total += d["messages_pruned"] or 0

    if baseline_total == 0:
        return []

    routing_saved = baseline_total - routed_total
    routing_pct = (routing_saved / baseline_total * 100) if baseline_total > 0 else 0

    pruning_cost_saved = registry.estimate_cost(
        baseline_model.name, pruned_tokens_total, 0
    ) or 0.0
    routing_plus_pruning_cost = routed_total - pruning_cost_saved
    routing_plus_pruning_saved = baseline_total - routing_plus_pruning_cost
    routing_plus_pruning_pct = (
        (routing_plus_pruning_saved / baseline_total * 100) if baseline_total > 0 else 0
    )

    rtk_input_reduction = 0.40
    ponytail_output_reduction = 0.30
    headroom_compression = 0.20

    stack_cost = 0.0
    for d in decisions:
        model = d["routed_model"] or d["request_model"]
        prompt = (d["prompt_tokens"] or 0) * (1 - rtk_input_reduction) * (1 - headroom_compression)
        completion = (d["completion_tokens"] or 0) * (1 - ponytail_output_reduction)
        stack_cost += registry.estimate_cost(model, int(prompt), int(completion)) or 0.0

    stack_cost -= pruning_cost_saved
    stack_saved = baseline_total - stack_cost
    stack_pct = (stack_saved / baseline_total * 100) if baseline_total > 0 else 0

    return [
        LayerResult("Baseline (all Opus)", baseline_total, 0.0, 0.0),
        LayerResult("Routing only", routed_total, routing_saved, routing_pct),
        LayerResult(
            "Routing + pruning",
            max(0, routing_plus_pruning_cost),
            routing_plus_pruning_saved,
            routing_plus_pruning_pct,
        ),
        LayerResult(
            "Full stack (est.)",
            max(0, stack_cost),
            stack_saved,
            stack_pct,
        ),
    ]


def _format_table(
    decisions: list[dict],
    layers: list[LayerResult],
) -> str:
    sessions = set(d["session_id"] for d in decisions if d["session_id"])
    total_requests = len(decisions)
    total_tokens = sum(d["total_tokens"] or 0 for d in decisions)

    lines = [
        "",
        "╭─ Costwise Validation ─────────────────────────────────────────╮",
        f"│  Sessions:  {len(sessions):>6}                                        │",
        f"│  Requests:  {total_requests:>6}                                        │",
        f"│  Tokens:    {total_tokens:>10,}                                    │",
        "├───────────────────────────┬───────────┬───────────┬───────────┤",
        "│ Layer                     │  Cost ($) │ Saved ($) │ Savings % │",
        "├───────────────────────────┼───────────┼───────────┼───────────┤",
    ]

    for layer in layers:
        name = layer.label.ljust(25)
        cost = f"${layer.cost_usd:>8.4f}"
        saved = f"${layer.saved_usd:>8.4f}" if layer.saved_usd > 0 else "       —"
        pct = f"{layer.savings_pct:>8.1f}%" if layer.savings_pct > 0 else "       —"
        lines.append(f"│ {name} │ {cost} │ {saved} │ {pct} │")

    lines.append("╰───────────────────────────┴───────────┴───────────┴───────────╯")

    if layers and layers[-1].savings_pct > 0:
        lines.append("")
        lines.append(
            f"  Full stack projection: {layers[-1].savings_pct:.1f}% savings "
            f"(RTK 40% input, Ponytail 30% output, Headroom 20% compression)"
        )
        lines.append("  Note: RTK/Ponytail/Headroom numbers are projections, not measured.")

    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate Costwise savings claims")
    parser.add_argument("--db", default=None, help="Path to tracking database")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    db_path = _find_db(args.db)
    decisions = _load_decisions(db_path)

    if not decisions:
        print("No routing decisions recorded yet.")
        print("Run the proxy with real traffic, then re-run this script.")
        sys.exit(0)

    registry = PricingRegistry()
    layers = _compute_layers(decisions, registry)

    if args.as_json:
        result = {
            "total_requests": len(decisions),
            "total_sessions": len(set(d["session_id"] for d in decisions if d["session_id"])),
            "layers": [
                {
                    "label": l.label,
                    "cost_usd": round(l.cost_usd, 6),
                    "saved_usd": round(l.saved_usd, 6),
                    "savings_pct": round(l.savings_pct, 2),
                }
                for l in layers
            ],
        }
        print(json.dumps(result, indent=2))
    else:
        print(_format_table(decisions, layers))


if __name__ == "__main__":
    main()
