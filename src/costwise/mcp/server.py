"""Costwise MCP server — exposes routing, budgeting, and stats as tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from costwise.config.loader import load_config
from costwise.config.schema import CostwiseConfig
from costwise.core.pricing import PricingRegistry
from costwise.core.router import Router, RouterConfig
from costwise.tracking.store import TrackingStore

logger = logging.getLogger(__name__)

mcp = FastMCP("costwise")

_config: CostwiseConfig | None = None
_store: TrackingStore | None = None
_router: Router | None = None
_registry: PricingRegistry | None = None


def _init() -> tuple[CostwiseConfig, TrackingStore, Router, PricingRegistry]:
    global _config, _store, _router, _registry
    if _config is None:
        _config = load_config()
    if _store is None:
        _store = TrackingStore(_config.tracking.db_path)
        _store._get_conn()
    if _registry is None:
        _registry = PricingRegistry()
    if _router is None:
        router_config = RouterConfig(
            enabled=_config.routing.enabled,
            enabled_providers=set(_config.routing.enabled_providers),
            min_confidence=_config.routing.min_confidence,
            default_output_ratio=_config.routing.default_output_ratio,
        )
        _router = Router(registry=_registry, config=router_config)
    return _config, _store, _router, _registry


@mcp.tool()
async def costwise_route(prompt: str, model: str = "claude-opus-4-7") -> str:
    """Classify a prompt and return the recommended model with cost estimate.

    Args:
        prompt: The prompt text to classify for complexity
        model: The model that would be used without routing (default: claude-opus-4-7)

    Returns:
        JSON with recommended_model, tier, confidence, reason, estimated_savings
    """
    _, _, router, _ = _init()

    request_body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }

    graph = None
    try:
        from costwise.graph.cache import GraphCache

        cache = GraphCache()
        graph = cache.get()
    except Exception:
        pass

    decision = router.route(request_body, graph=graph)

    result = {
        "recommended_model": decision.routed_model,
        "original_model": decision.original_model,
        "tier": decision.tier.value,
        "confidence": round(decision.confidence, 3),
        "reason": decision.reason,
        "is_rerouted": decision.is_rerouted,
        "estimated_savings_usd": round(decision.estimated_savings_usd, 6),
        "provider": decision.provider,
        "budget_action": decision.budget_action,
    }

    if decision.cost_estimate:
        result["estimated_cost_usd"] = round(decision.cost_estimate.estimated_total_cost, 6)
    if decision.fallback_chain:
        result["fallback_chain"] = decision.fallback_chain

    return json.dumps(result, indent=2)


@mcp.tool()
async def costwise_budget(files: list[str], token_budget: int = 15000) -> str:
    """Graph-guided context budget — rank files by relevance and suggest what to prune.

    Args:
        files: List of file paths to analyze for relevance
        token_budget: Maximum token budget for context (default: 15000)

    Returns:
        JSON with relevant_files sorted by score, prunable_files, estimated_token_savings
    """
    _init()

    graph = None
    try:
        from costwise.graph.cache import GraphCache

        cache = GraphCache()
        graph = cache.get()
    except Exception:
        pass

    if graph is None:
        return json.dumps({
            "status": "no_graph",
            "message": (
                "No code graph available."
                " Run Graphify first to enable graph-guided budgeting."
            ),
            "files": files,
        }, indent=2)

    try:
        from costwise.graph.relevance import score_relevance

        query_text = " ".join(files)
        result = score_relevance(graph, query_text)

        file_scores = []
        for f in files:
            best_score = 0.0
            for node_label, score in result.scores.items():
                if f in str(node_label) or str(node_label) in f:
                    best_score = max(best_score, score)
            file_scores.append({"file": f, "relevance": round(best_score, 4)})

        file_scores.sort(key=lambda x: x["relevance"], reverse=True)

        threshold = 0.1
        relevant = [f for f in file_scores if f["relevance"] >= threshold]
        prunable = [f for f in file_scores if f["relevance"] < threshold]

        return json.dumps({
            "status": "ok",
            "token_budget": token_budget,
            "relevant_files": relevant,
            "prunable_files": prunable,
            "recommendation": (
                f"Keep {len(relevant)} files,"
                f" prune {len(prunable)} for budget savings"
            ),
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
            "files": files,
        }, indent=2)


@mcp.tool()
async def costwise_stats(session_id: str | None = None) -> str:
    """Session cost, savings, and model distribution.

    Args:
        session_id: Optional session ID to filter (all sessions if omitted)

    Returns:
        JSON with total_cost, total_saved, savings_pct, model_breakdown, request_count
    """
    _, store, _, _ = _init()

    await store.initialize()

    stats = await store.get_session_stats(session_id)
    spend = await store.get_hourly_spend()
    model_dist = await store.get_model_distribution()
    tier_dist = await store.get_tier_distribution()

    total_cost = sum(float(s.get("total_cost") or 0) for s in stats)
    total_saved = sum(float(s.get("total_saved") or 0) for s in stats)
    total_requests = sum(int(s.get("request_count") or 0) for s in stats)
    savings_pct = (
        (total_saved / (total_cost + total_saved) * 100)
        if (total_cost + total_saved) > 0
        else 0
    )

    return json.dumps({
        "total_cost_usd": round(total_cost, 4),
        "total_saved_usd": round(total_saved, 4),
        "savings_pct": round(savings_pct, 1),
        "total_requests": total_requests,
        "hourly_spend_usd": round(spend, 4),
        "model_distribution": model_dist,
        "tier_distribution": tier_dist,
        "session_id": session_id,
    }, indent=2)


@mcp.tool()
async def costwise_gain() -> str:
    """Cumulative savings summary across all cost optimization layers.

    Returns:
        JSON with per-layer breakdown: routing, pruning, RTK, Ponytail, and total savings
    """
    config, store, _, _ = _init()
    await store.initialize()

    gain = await store.get_gain_summary()
    breakdown = await store.get_savings_breakdown()

    result: dict[str, Any] = {
        "total_requests": gain.get("total_requests") or 0,
        "total_cost_usd": round(float(gain.get("total_cost_usd") or 0), 4),
        "layers": {
            "routing": {
                "saved_usd": round(float(breakdown.get("routing_saved_usd") or 0), 4),
                "description": "Savings from routing to cheaper models",
            },
            "pruning": {
                "tokens_pruned": int(breakdown.get("total_tokens_pruned") or 0),
                "messages_pruned": int(breakdown.get("total_messages_pruned") or 0),
                "description": "Context reduction via graph-guided pruning",
            },
        },
        "period": {
            "first_request": gain.get("first_request"),
            "last_request": gain.get("last_request"),
        },
    }

    if config.integrations.rtk_enabled:
        try:
            from costwise.integrations.rtk import RtkReader

            reader = RtkReader(config.integrations.rtk_db_path)
            if reader.available:
                rtk = reader.get_summary()
                result["layers"]["rtk"] = {
                    "total_commands": rtk.total_commands,
                    "saved_tokens": rtk.total_saved_tokens,
                    "avg_savings_pct": rtk.avg_savings_pct,
                    "description": "CLI output filtering savings",
                }
                reader.close()
        except Exception:
            logger.debug("RTK data unavailable for gain report", exc_info=True)

    if config.integrations.ponytail_enabled:
        try:
            from costwise.integrations.ponytail import PonytailReader

            reader = PonytailReader(config.integrations.ponytail_config_path)
            pt_config = reader.get_config()
            if pt_config.enabled:
                result["layers"]["ponytail"] = {
                    "mode": pt_config.mode,
                    "output_savings_ratio": pt_config.output_savings_ratio,
                    "description": "Output token reduction via behavior shaping",
                }
        except Exception:
            logger.debug("Ponytail data unavailable for gain report", exc_info=True)

    if config.integrations.headroom_enabled:
        try:
            from costwise.integrations.headroom import is_available

            result["layers"]["headroom"] = {
                "available": is_available(),
                "description": "Token-level message compression",
            }
        except Exception:
            pass

    total_saved = float(breakdown.get("routing_saved_usd") or 0)
    total_cost = float(gain.get("total_cost_usd") or 0)
    result["total_saved_usd"] = round(total_saved, 4)
    result["overall_savings_pct"] = (
        round(total_saved / (total_cost + total_saved) * 100, 1)
        if (total_cost + total_saved) > 0
        else 0
    )

    return json.dumps(result, indent=2)


@mcp.tool()
async def costwise_feedback(window_minutes: int = 60) -> str:
    """Get routing quality feedback metrics.

    Args:
        window_minutes: Time window for metrics (default: 60 minutes)

    Returns:
        JSON with retry_rate, false_downgrade_rate, quality_grade (A/B/C/D/F),
        threshold history, and current thresholds.
    """
    config, store, router, _ = _init()
    await store.initialize()

    from costwise.feedback.metrics import FeedbackMetrics

    metrics = FeedbackMetrics(store)
    summary = await metrics.get_summary(
        window_minutes=window_minutes,
        current_simple_threshold=router.config.classifier.simple_threshold,
        current_complex_threshold=router.config.classifier.complex_threshold,
    )

    return json.dumps(summary, indent=2, default=str)
