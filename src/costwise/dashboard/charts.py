"""Pure Python SVG chart generators for the Costwise dashboard."""

from __future__ import annotations

import math
from typing import Any

COLORS = [
    "#6ee7b7",  # emerald
    "#93c5fd",  # blue
    "#fbbf24",  # amber
    "#f87171",  # red
    "#a78bfa",  # violet
    "#fb923c",  # orange
    "#34d399",  # green
    "#f472b6",  # pink
]

BG_COLOR = "#1e1e2e"
GRID_COLOR = "#313244"
TEXT_COLOR = "#cdd6f4"
MUTED_COLOR = "#6c7086"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cost_bar_chart(
    hourly_data: list[dict[str, Any]],
    width: int = 600,
    height: int = 200,
) -> str:
    if not hourly_data:
        return _empty_chart(width, height, "No cost data yet")

    pad_left, pad_right, pad_top, pad_bottom = 60, 20, 20, 40
    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bottom

    costs = [float(d.get("cost") or 0) for d in hourly_data]
    saveds = [float(d.get("saved") or 0) for d in hourly_data]
    max_val = max(max(costs, default=0), max(c + s for c, s in zip(costs, saveds)), 0.01)

    n = len(hourly_data)
    bar_w = max(4, chart_w / max(n, 1) - 2)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:{BG_COLOR};border-radius:8px">',
    ]

    for i in range(5):
        y = pad_top + chart_h * i / 4
        val = max_val * (1 - i / 4)
        parts.append(
            f'<line x1="{pad_left}" y1="{y}" x2="{width - pad_right}" y2="{y}" '
            f'stroke="{GRID_COLOR}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + 4}" text-anchor="end" '
            f'fill="{MUTED_COLOR}" font-size="10">${val:.2f}</text>'
        )

    for i, d in enumerate(hourly_data):
        x = pad_left + i * (chart_w / n) + (chart_w / n - bar_w) / 2
        cost = float(d.get("cost") or 0)
        saved = float(d.get("saved") or 0)

        cost_h = (cost / max_val) * chart_h
        saved_h = (saved / max_val) * chart_h

        if saved_h > 0:
            parts.append(
                f'<rect x="{x}" y="{pad_top + chart_h - cost_h - saved_h}" '
                f'width="{bar_w}" height="{saved_h}" fill="{COLORS[0]}" opacity="0.4" rx="2"/>'
            )
        if cost_h > 0:
            parts.append(
                f'<rect x="{x}" y="{pad_top + chart_h - cost_h}" '
                f'width="{bar_w}" height="{cost_h}" fill="{COLORS[1]}" rx="2"/>'
            )

        hour_label = str(d.get("hour", ""))[-5:-3] if d.get("hour") else ""
        if i % max(1, n // 8) == 0 and hour_label:
            parts.append(
                f'<text x="{x + bar_w / 2}" y="{height - pad_bottom + 15}" '
                f'text-anchor="middle" fill="{MUTED_COLOR}" font-size="10">{hour_label}h</text>'
            )

    parts.append(
        f'<rect x="{pad_left}" y="{height - 18}" width="10" height="10" fill="{COLORS[1]}" rx="2"/>'
        f'<text x="{pad_left + 14}" y="{height - 9}" fill="{TEXT_COLOR}" font-size="10">Cost</text>'
        f'<rect x="{pad_left + 55}" y="{height - 18}" width="10" height="10" fill="{COLORS[0]}" '
        f'opacity="0.4" rx="2"/>'
        f'<text x="{pad_left + 69}" y="{height - 9}" fill="{TEXT_COLOR}" font-size="10">Saved</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def model_donut_chart(
    distribution: list[dict[str, Any]],
    width: int = 300,
    height: int = 300,
) -> str:
    if not distribution:
        return _empty_chart(width, height, "No model data yet")

    cx, cy = width / 2, height / 2 - 20
    outer_r = min(cx, cy) - 30
    inner_r = outer_r * 0.6

    total = sum(int(d.get("count") or 0) for d in distribution)
    if total == 0:
        return _empty_chart(width, height, "No requests recorded")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:{BG_COLOR};border-radius:8px">',
    ]

    angle = -math.pi / 2
    for i, d in enumerate(distribution):
        count = int(d.get("count") or 0)
        if count == 0:
            continue
        frac = count / total
        sweep = frac * 2 * math.pi
        color = COLORS[i % len(COLORS)]

        x1_o = cx + outer_r * math.cos(angle)
        y1_o = cy + outer_r * math.sin(angle)
        x2_o = cx + outer_r * math.cos(angle + sweep)
        y2_o = cy + outer_r * math.sin(angle + sweep)

        x1_i = cx + inner_r * math.cos(angle + sweep)
        y1_i = cy + inner_r * math.sin(angle + sweep)
        x2_i = cx + inner_r * math.cos(angle)
        y2_i = cy + inner_r * math.sin(angle)

        large = 1 if sweep > math.pi else 0

        path = (
            f"M {x1_o:.1f} {y1_o:.1f} "
            f"A {outer_r:.1f} {outer_r:.1f} 0 {large} 1 {x2_o:.1f} {y2_o:.1f} "
            f"L {x1_i:.1f} {y1_i:.1f} "
            f"A {inner_r:.1f} {inner_r:.1f} 0 {large} 0 {x2_i:.1f} {y2_i:.1f} Z"
        )
        parts.append(f'<path d="{path}" fill="{color}"/>')
        angle += sweep

    parts.append(
        f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" fill="{TEXT_COLOR}" '
        f'font-size="20" font-weight="bold">{total}</text>'
        f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" fill="{MUTED_COLOR}" '
        f'font-size="11">requests</text>'
    )

    legend_y = height - 20
    legend_x = 10
    for i, d in enumerate(distribution[:6]):
        model = _escape(str(d.get("routed_model") or "unknown"))
        if len(model) > 20:
            model = model[:18] + ".."
        count = int(d.get("count") or 0)
        color = COLORS[i % len(COLORS)]
        x = legend_x + (i % 3) * (width // 3)
        y = legend_y - (1 - i // 3) * 16
        parts.append(
            f'<rect x="{x}" y="{y - 8}" width="8" height="8" fill="{color}" rx="2"/>'
            f'<text x="{x + 12}" y="{y}" fill="{TEXT_COLOR}" font-size="9">'
            f"{model} ({count})</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


def savings_stacked_bars(
    breakdown: dict[str, Any],
    width: int = 500,
    height: int = 120,
) -> str:
    routing = float(breakdown.get("routing_saved_usd") or 0)
    pruning_tokens = int(breakdown.get("total_tokens_pruned") or 0)
    pruning_est = pruning_tokens * 0.000015

    layers = [
        ("Routing", routing, COLORS[0]),
        ("Pruning", pruning_est, COLORS[1]),
    ]

    total = sum(v for _, v, _ in layers)
    if total <= 0:
        return _empty_chart(width, height, "No savings recorded yet")

    pad_left, pad_right = 80, 20
    bar_h = 28
    chart_w = width - pad_left - pad_right

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:{BG_COLOR};border-radius:8px">',
    ]

    y = 20
    for label, value, color in layers:
        bar_w = (value / total) * chart_w if total > 0 else 0
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + bar_h / 2 + 4}" text-anchor="end" '
            f'fill="{TEXT_COLOR}" font-size="11">{label}</text>'
        )
        if bar_w > 0:
            parts.append(
                f'<rect x="{pad_left}" y="{y}" width="{bar_w}" height="{bar_h}" '
                f'fill="{color}" rx="4"/>'
            )
            parts.append(
                f'<text x="{pad_left + bar_w + 6}" y="{y + bar_h / 2 + 4}" '
                f'fill="{MUTED_COLOR}" font-size="10">${value:.2f}</text>'
            )
        y += bar_h + 8

    parts.append(
        f'<text x="{pad_left}" y="{y + 10}" fill="{TEXT_COLOR}" font-size="12" '
        f'font-weight="bold">Total saved: ${total:.2f}</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def budget_gauge(
    current_pct: float,
    width: int = 200,
    height: int = 120,
) -> str:
    cx, cy = width / 2, height - 20
    r = min(cx - 10, cy - 10)

    pct = max(0.0, min(100.0, current_pct))
    sweep_angle = math.pi * (pct / 100)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:{BG_COLOR};border-radius:8px">',
    ]

    bg_x1 = cx - r
    bg_x2 = cx + r
    parts.append(
        f'<path d="M {bg_x1} {cy} A {r} {r} 0 0 1 {bg_x2} {cy}" '
        f'fill="none" stroke="{GRID_COLOR}" stroke-width="12" stroke-linecap="round"/>'
    )

    if pct > 0:
        end_x = cx - r * math.cos(sweep_angle)
        end_y = cy - r * math.sin(sweep_angle)
        large = 1 if sweep_angle > math.pi / 2 else 0
        color = COLORS[0] if pct < 60 else (COLORS[3] if pct > 90 else COLORS[2])
        parts.append(
            f'<path d="M {bg_x1} {cy} A {r} {r} 0 {large} 1 {end_x:.1f} {end_y:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="12" stroke-linecap="round"/>'
        )

    parts.append(
        f'<text x="{cx}" y="{cy - 8}" text-anchor="middle" fill="{TEXT_COLOR}" '
        f'font-size="18" font-weight="bold">{pct:.0f}%</text>'
        f'<text x="{cx}" y="{cy + 8}" text-anchor="middle" fill="{MUTED_COLOR}" '
        f'font-size="10">budget used</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def quality_gauge(
    false_downgrade_pct: float,
    target_pct: float = 3.0,
    width: int = 200,
    height: int = 120,
) -> str:
    """SVG gauge for false-downgrade rate. Green = good (low rate), red = bad."""
    cx, cy = width / 2, height - 20
    r = min(cx - 10, cy - 10)

    max_display = target_pct * 3
    pct = max(0.0, min(max_display, false_downgrade_pct))
    sweep_angle = math.pi * (pct / max_display)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:{BG_COLOR};border-radius:8px">',
    ]

    bg_x1 = cx - r
    bg_x2 = cx + r
    parts.append(
        f'<path d="M {bg_x1} {cy} A {r} {r} 0 0 1 {bg_x2} {cy}" '
        f'fill="none" stroke="{GRID_COLOR}" stroke-width="12" stroke-linecap="round"/>'
    )

    if pct > 0:
        end_x = cx - r * math.cos(sweep_angle)
        end_y = cy - r * math.sin(sweep_angle)
        large = 1 if sweep_angle > math.pi / 2 else 0
        color = COLORS[0] if pct < target_pct else (COLORS[3] if pct > target_pct * 2 else COLORS[2])
        parts.append(
            f'<path d="M {bg_x1} {cy} A {r} {r} 0 {large} 1 {end_x:.1f} {end_y:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="12" stroke-linecap="round"/>'
        )

    grade_map = {"A": COLORS[0], "B": COLORS[1], "C": COLORS[2], "D": COLORS[5], "F": COLORS[3]}
    if false_downgrade_pct < 0.01:
        grade, grade_color = "A", grade_map["A"]
    elif false_downgrade_pct < 0.02:
        grade, grade_color = "B", grade_map["B"]
    elif false_downgrade_pct < 0.03:
        grade, grade_color = "C", grade_map["C"]
    elif false_downgrade_pct < 0.05:
        grade, grade_color = "D", grade_map["D"]
    else:
        grade, grade_color = "F", grade_map["F"]

    parts.append(
        f'<text x="{cx}" y="{cy - 12}" text-anchor="middle" fill="{grade_color}" '
        f'font-size="22" font-weight="bold">{grade}</text>'
        f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" fill="{TEXT_COLOR}" '
        f'font-size="11">{false_downgrade_pct * 100:.1f}%</text>'
        f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" fill="{MUTED_COLOR}" '
        f'font-size="9">false downgrades</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def _empty_chart(width: int, height: int, message: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="background:{BG_COLOR};border-radius:8px">'
        f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
        f'fill="{MUTED_COLOR}" font-size="13">{_escape(message)}</text>'
        f"</svg>"
    )
