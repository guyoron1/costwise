"""FastAPI + HTMX dashboard for Costwise cost intelligence."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import costwise
from costwise.config.schema import CostwiseConfig
from costwise.dashboard.charts import (
    budget_gauge,
    cost_bar_chart,
    model_donut_chart,
    quality_gauge,
    savings_stacked_bars,
)
from costwise.dashboard.data import DashboardDataCollector
from costwise.proxy.health import router as health_router
from costwise.tracking.store import TrackingStore

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


def create_dashboard_app(config: CostwiseConfig, store: TrackingStore) -> FastAPI:
    collector = DashboardDataCollector(store, config)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await store.initialize()
        yield
        store.close()

    app = FastAPI(
        title="Costwise Dashboard",
        version=costwise.__version__,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(health_router)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        data = await collector.collect()
        ctx = _build_template_context(data)
        return templates.TemplateResponse(request, "dashboard.html", ctx)

    @app.get("/api/summary")
    async def api_summary() -> JSONResponse:
        data = await collector.collect()
        return JSONResponse(_serialize_dashboard(data))

    @app.get("/api/requests")
    async def api_requests() -> JSONResponse:
        reqs = await store.get_recent_requests(20)
        return JSONResponse(reqs)

    @app.get("/api/costs")
    async def api_costs() -> JSONResponse:
        costs = await store.get_hourly_cost_series(24)
        return JSONResponse(costs)

    @app.get("/api/models")
    async def api_models() -> JSONResponse:
        dist = await store.get_model_distribution()
        return JSONResponse(dist)

    @app.get("/api/health")
    async def api_health() -> JSONResponse:
        data = await collector.collect()
        return JSONResponse(
            {k: _snapshot_to_dict(v) for k, v in data.provider_health.items()}
        )

    @app.get("/api/budget")
    async def api_budget() -> JSONResponse:
        alerts = await store.get_budget_alerts(10)
        spend = await store.get_hourly_spend()
        return JSONResponse({"hourly_spend": spend, "alerts": alerts})

    @app.get("/partials/requests", response_class=HTMLResponse)
    async def partial_requests(request: Request) -> HTMLResponse:
        reqs = await store.get_recent_requests(20)
        return templates.TemplateResponse(
            request, "partials/requests.html",
            {"recent_requests": reqs},
        )

    @app.get("/partials/costs", response_class=HTMLResponse)
    async def partial_costs(request: Request) -> HTMLResponse:
        costs = await store.get_hourly_cost_series(24)
        chart = cost_bar_chart(costs)
        return templates.TemplateResponse(
            request, "partials/costs.html",
            {"cost_chart": chart},
        )

    @app.get("/partials/models", response_class=HTMLResponse)
    async def partial_models(request: Request) -> HTMLResponse:
        dist = await store.get_model_distribution()
        chart = model_donut_chart(dist)
        return templates.TemplateResponse(
            request, "partials/models.html",
            {"model_chart": chart},
        )

    @app.get("/partials/savings", response_class=HTMLResponse)
    async def partial_savings(request: Request) -> HTMLResponse:
        breakdown = await store.get_savings_breakdown()
        chart = savings_stacked_bars(breakdown)
        return templates.TemplateResponse(
            request, "partials/savings.html",
            {"savings_chart": chart},
        )

    @app.get("/api/feedback")
    async def api_feedback() -> JSONResponse:
        summary = await store.get_feedback_summary()
        return JSONResponse(summary)

    @app.get("/partials/feedback", response_class=HTMLResponse)
    async def partial_feedback(request: Request) -> HTMLResponse:
        summary = await store.get_feedback_summary()
        fdr = float(summary.get("false_downgrade_rate") or 0)
        chart = quality_gauge(fdr)
        return templates.TemplateResponse(
            request, "partials/feedback.html",
            {"quality_chart": chart, "feedback": summary},
        )

    @app.get("/partials/budget", response_class=HTMLResponse)
    async def partial_budget(request: Request) -> HTMLResponse:
        alerts = await store.get_budget_alerts(5)
        spend = await store.get_hourly_spend()
        hourly_limit = config.budget.max_hourly_usd
        pct = (spend / hourly_limit * 100) if hourly_limit and hourly_limit > 0 else 0
        chart = budget_gauge(pct)
        return templates.TemplateResponse(
            request, "partials/budget.html",
            {"budget_chart": chart, "budget_alerts": alerts},
        )

    return app


def _build_template_context(data: Any) -> dict[str, Any]:
    gain = data.gain_summary or {}
    return {
        "version": costwise.__version__,
        "gain": gain,
        "hourly_spend": data.hourly_spend,
        "cost_chart": cost_bar_chart(data.hourly_costs),
        "model_chart": model_donut_chart(data.model_distribution),
        "savings_chart": savings_stacked_bars(data.savings_breakdown),
        "budget_chart": budget_gauge(
            (data.hourly_spend / gain.get("max_hourly_usd", 1) * 100)
            if gain.get("max_hourly_usd")
            else 0
        ),
        "quality_chart": quality_gauge(
            float(data.feedback_summary.get("false_downgrade_rate") or 0)
        ),
        "feedback": data.feedback_summary,
        "recent_requests": data.recent_requests,
        "integrations": bool(data.rtk_summary or data.ponytail_config or data.headroom_available),
        "rtk": data.rtk_summary,
        "ponytail": data.ponytail_config,
        "headroom_available": data.headroom_available,
    }


def _serialize_dashboard(data: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "gain_summary": data.gain_summary,
        "recent_requests": data.recent_requests,
        "model_distribution": data.model_distribution,
        "tier_distribution": data.tier_distribution,
        "hourly_costs": data.hourly_costs,
        "savings_breakdown": data.savings_breakdown,
        "budget_alerts": data.budget_alerts,
        "hourly_spend": data.hourly_spend,
        "headroom_available": data.headroom_available,
        "feedback_summary": data.feedback_summary,
    }
    if data.rtk_summary:
        result["rtk"] = {
            "total_commands": data.rtk_summary.total_commands,
            "total_saved_tokens": data.rtk_summary.total_saved_tokens,
            "avg_savings_pct": data.rtk_summary.avg_savings_pct,
        }
    if data.ponytail_config:
        result["ponytail"] = {
            "mode": data.ponytail_config.mode,
            "enabled": data.ponytail_config.enabled,
            "output_savings_ratio": data.ponytail_config.output_savings_ratio,
        }
    return result


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "provider": snapshot.provider,
        "status": snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status),
        "total_requests": snapshot.total_requests,
        "error_count": snapshot.error_count,
        "rate_limit_count": snapshot.rate_limit_count,
        "avg_latency_ms": snapshot.avg_latency_ms,
        "error_rate": snapshot.error_rate,
    }
