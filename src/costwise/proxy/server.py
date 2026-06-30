"""FastAPI ASGI proxy — classify, route, budget-check, and forward LLM requests."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from costwise.config.schema import CostwiseConfig
from costwise.core.budget import BudgetAction, BudgetEnforcer
from costwise.core.classifier import ClassifierConfig
from costwise.core.health import ProviderHealthTracker
from costwise.core.models import RoutingDecision, Tier
from costwise.core.pricing import PricingRegistry
from costwise.core.router import Router, RouterConfig
from costwise.feedback.detector import RetryDetector, RetryEvent
from costwise.feedback.fingerprint import fingerprint as compute_fingerprint
from costwise.feedback.tuner import ThresholdTuner
from costwise.graph.cache import GraphCache
from costwise.graph.pruner import PruneResult, prune_context
from costwise.integrations.headroom import CompressionResult, compress_messages, is_available as headroom_available
from costwise.proxy.health import router as health_router, set_ready
from costwise.proxy.translator import ApiFormat, anthropic_to_openai, detect_format, openai_to_anthropic
from costwise.tracking.store import RoutingRecord, TrackingStore

logger = logging.getLogger("costwise.proxy")

_MAX_FALLBACK_RETRIES = 3


def _extract_usage(body: dict) -> tuple[int | None, int | None, int | None]:
    usage = body.get("usage", {})
    prompt = usage.get("input_tokens") or usage.get("prompt_tokens")
    completion = usage.get("output_tokens") or usage.get("completion_tokens")
    total = None
    if prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, completion, total


def _extract_stream_usage(data: str) -> tuple[int | None, int | None, int | None]:
    try:
        event = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None, None, None

    if event.get("type") == "message_delta":
        return _extract_usage(event)
    if "usage" in event:
        return _extract_usage(event)
    return None, None, None


def _build_router(config: CostwiseConfig, health_tracker: ProviderHealthTracker, budget_enforcer: BudgetEnforcer) -> Router:
    classifier_cfg = ClassifierConfig(
        simple_threshold=config.routing.simple_threshold,
        complex_threshold=config.routing.complex_threshold,
    )
    router_cfg = RouterConfig(
        enabled=config.routing.enabled,
        classifier=classifier_cfg,
        enabled_providers=set(config.routing.enabled_providers),
        min_confidence=config.routing.min_confidence,
        default_output_ratio=config.routing.default_output_ratio,
    )
    return Router(
        registry=PricingRegistry(),
        config=router_cfg,
        health_tracker=health_tracker,
        budget_enforcer=budget_enforcer,
    )


def _build_record(
    decision: RoutingDecision,
    endpoint: str,
    session_id: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    latency_ms: float,
    status_code: int,
    prune_result: PruneResult | None = None,
    content_hash: str | None = None,
) -> RoutingRecord:
    return RoutingRecord(
        endpoint=endpoint,
        request_model=decision.original_model,
        routed_model=decision.routed_model if decision.is_rerouted else None,
        tier=decision.tier.value,
        session_id=session_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=decision.cost_estimate.estimated_total_cost if decision.cost_estimate else None,
        saved_usd=decision.estimated_savings_usd if decision.is_rerouted else None,
        latency_ms=latency_ms,
        classification=decision.reason,
        provider=decision.provider,
        status_code=status_code,
        tokens_pruned=prune_result.tokens_saved if prune_result else None,
        messages_pruned=prune_result.dropped_entries if prune_result else None,
        content_hash=content_hash,
    )


def _build_graph_cache(config: CostwiseConfig) -> GraphCache:
    if not config.graph.enabled:
        return GraphCache()
    return GraphCache(config.graph.graph_path)


_TIER_UPGRADE = {Tier.SIMPLE: Tier.MEDIUM, Tier.MEDIUM: Tier.COMPLEX}


def create_app(config: CostwiseConfig, store: TrackingStore) -> FastAPI:
    health_tracker = ProviderHealthTracker()
    budget_enforcer = BudgetEnforcer(config.budget)
    router = _build_router(config, health_tracker, budget_enforcer)
    graph_cache = _build_graph_cache(config)

    retry_detector: RetryDetector | None = None
    tuner: ThresholdTuner | None = None
    if config.feedback.enabled:
        retry_detector = RetryDetector(
            store,
            window_minutes=config.feedback.retry_window_minutes,
            similarity_threshold=config.feedback.similarity_threshold,
        )
        tuner = ThresholdTuner(router.config.classifier, config.feedback, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await store.initialize()
        graph_cache.get()
        set_ready(True)
        yield
        set_ready(False)
        store.close()

    app = FastAPI(title="Costwise Proxy", version="0.3.0", lifespan=lifespan)
    app.include_router(health_router)

    clients: dict[str, httpx.AsyncClient] = {}

    def _get_client(api_base: str) -> httpx.AsyncClient:
        if api_base not in clients:
            clients[api_base] = httpx.AsyncClient(
                base_url=api_base,
                timeout=httpx.Timeout(config.proxy.timeout_s, connect=10.0),
            )
        return clients[api_base]

    default_client = _get_client(config.proxy.upstream)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        response_model=None,
    )
    async def proxy_request(request: Request, path: str) -> StreamingResponse | JSONResponse:
        start = time.monotonic()
        session_id = request.headers.get("x-session-id", str(uuid.uuid4())[:8])

        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        body_bytes = await request.body()
        request_body = {}
        if body_bytes:
            try:
                request_body = json.loads(body_bytes)
            except json.JSONDecodeError:
                pass

        target_url = f"/{path}"
        is_streaming = request_body.get("stream", False)

        # ── Fingerprint + Retry Detection ────────────────
        content_hash: str | None = None
        retry_event = None
        messages = request_body.get("messages", [])
        if retry_detector and messages:
            content_hash = compute_fingerprint(messages)
            retry_event = await retry_detector.check(session_id, messages, content_hash)

        if tuner:
            tuner.record_request()

        # ── Classification + Routing + Budget Check ──────
        graph = graph_cache.get()
        decision = router.route(request_body, graph=graph)

        # ── Retry Override: don't repeat a failed downgrade ──
        if retry_event and retry_event.was_downgraded and decision.is_rerouted:
            upgrade_to = _TIER_UPGRADE.get(decision.tier)
            if upgrade_to:
                decision = decision.model_copy(update={
                    "routed_model": decision.original_model,
                    "tier": upgrade_to,
                    "reason": f"retry override ({retry_event.original_tier}→{upgrade_to.value}): {decision.reason}",
                })
                logger.info(
                    "Retry detected for session %s, upgrading %s → %s",
                    session_id, retry_event.original_tier, upgrade_to.value,
                )

        # Budget blocked — return 429 with budget info
        if decision.budget_action == BudgetAction.BLOCK.value:
            return JSONResponse(
                status_code=429,
                content={"error": {"type": "budget_exceeded", "message": decision.budget_warning}},
                headers={
                    "x-costwise-budget-action": decision.budget_action,
                    "x-costwise-budget-warning": decision.budget_warning,
                },
            )

        # ── Graph-Guided Context Pruning ─────────────────
        prune_result: PruneResult | None = None
        if graph and config.graph.enabled and "messages" in request_body:
            pruned_msgs, prune_result = prune_context(
                request_body["messages"],
                graph,
                threshold=config.graph.relevance_threshold,
                max_hops=config.graph.max_hops,
                decay=config.graph.decay,
                community_boost=config.graph.community_boost,
                protect_last_n=config.graph.protect_last_n,
            )
            if prune_result.dropped_entries > 0:
                request_body = {**request_body, "messages": pruned_msgs}

        # ── Headroom Compression ─────────────────────
        compression_result: CompressionResult | None = None
        if (
            config.integrations.headroom_enabled
            and headroom_available()
            and "messages" in request_body
        ):
            compression_result = compress_messages(
                request_body["messages"],
                model=decision.routed_model,
            )
            if compression_result.applied and compression_result.tokens_saved > 0:
                request_body = {**request_body, "messages": compression_result.messages}

        # Prepare request for forwarding
        client, send_body, send_url = _prepare_forward(
            config, decision, request_body, target_url, default_client, _get_client
        )
        send_bytes = json.dumps(send_body).encode() if send_body else body_bytes
        headers["content-type"] = "application/json"

        if is_streaming:
            return await _handle_streaming(
                client, send_url, headers, send_bytes,
                decision, session_id, start, store,
                health_tracker=health_tracker,
                budget_enforcer=budget_enforcer,
                prune_result=prune_result,
                content_hash=content_hash,
                retry_event=retry_event,
                tuner=tuner,
            )

        # ── Non-streaming with fallback retry ────────────
        upstream_resp = await client.request(
            method=request.method,
            url=send_url,
            headers=headers,
            content=send_bytes,
        )

        # 429 fallback: try alternatives from the fallback chain
        if upstream_resp.status_code == 429 and decision.fallback_chain:
            latency_ms = (time.monotonic() - start) * 1000
            health_tracker.record_rate_limit(decision.provider, latency_ms)
            await store.record_provider_health(
                decision.provider, decision.routed_model, latency_ms, 429, rate_limited=True,
            )
            logger.info("Rate limited by %s, trying fallback chain", decision.provider)

            registry = router.registry
            for fallback_model_name in decision.fallback_chain[:_MAX_FALLBACK_RETRIES]:
                fb_info = registry.get(fallback_model_name)
                if not fb_info:
                    continue

                fb_body = dict(send_body) if send_body else {}
                fb_body["model"] = fallback_model_name
                fb_api_base = router.config.provider_api_bases.get(fb_info.provider, "")
                fb_client = _get_client(fb_api_base) if fb_api_base else default_client

                fb_bytes = json.dumps(fb_body).encode()
                upstream_resp = await fb_client.request(
                    method=request.method,
                    url=send_url,
                    headers=headers,
                    content=fb_bytes,
                )

                if upstream_resp.status_code != 429:
                    decision = decision.model_copy(update={
                        "routed_model": fallback_model_name,
                        "provider": fb_info.provider,
                        "reason": f"fallback from {decision.routed_model} (429)",
                    })
                    break

                health_tracker.record_rate_limit(fb_info.provider)
                await store.record_provider_health(
                    fb_info.provider, fallback_model_name, 0.0, 429, rate_limited=True,
                )

        latency_ms = (time.monotonic() - start) * 1000
        response_body = {}
        try:
            response_body = upstream_resp.json()
        except (json.JSONDecodeError, ValueError):
            pass

        prompt, completion, total = _extract_usage(response_body)

        # Record provider health
        is_error = upstream_resp.status_code >= 400
        if upstream_resp.status_code == 429:
            health_tracker.record_rate_limit(decision.provider, latency_ms)
        elif is_error:
            health_tracker.record_error(
                decision.provider, latency_ms, upstream_resp.status_code,
                error=str(response_body.get("error", "")),
            )
        else:
            health_tracker.record_success(decision.provider, latency_ms, upstream_resp.status_code)

        await store.record_provider_health(
            decision.provider, decision.routed_model,
            latency_ms, upstream_resp.status_code,
            rate_limited=upstream_resp.status_code == 429,
            error=str(response_body.get("error", "")) if is_error else None,
        )

        # Record spend for budget tracking
        cost_usd = 0.0
        if decision.cost_estimate and not is_error:
            cost_usd = decision.cost_estimate.estimated_total_cost
            budget_enforcer.record_spend(cost_usd)

        record = _build_record(
            decision, target_url, session_id,
            prompt, completion, total,
            latency_ms, upstream_resp.status_code,
            prune_result=prune_result,
            content_hash=content_hash,
        )
        request_id = await store.record_request(record)

        # ── Feedback: record retry event + nudge tuner ───
        if retry_event and retry_event.was_downgraded:
            await store.record_retry_event(
                session_id=session_id,
                original_request_id=retry_event.original_request_id,
                retry_request_id=request_id,
                content_hash=retry_event.content_hash,
                similarity_score=retry_event.similarity_score,
                original_tier=retry_event.original_tier,
                original_model=retry_event.original_model,
                time_delta_s=retry_event.time_delta_s,
                was_downgraded=True,
            )
            if tuner:
                await tuner.on_retry(retry_event)

        resp_headers = dict(upstream_resp.headers)
        if decision.is_rerouted:
            resp_headers["x-costwise-routed"] = decision.routed_model
            resp_headers["x-costwise-tier"] = decision.tier.value
        if prune_result and prune_result.dropped_entries > 0:
            resp_headers["x-costwise-pruned"] = str(prune_result.tokens_saved)
        if compression_result and compression_result.applied:
            resp_headers["x-costwise-compressed"] = str(compression_result.tokens_saved)
        if decision.budget_warning:
            resp_headers["x-costwise-budget-action"] = decision.budget_action
            resp_headers["x-costwise-budget-warning"] = decision.budget_warning

        return JSONResponse(
            content=response_body or upstream_resp.text,
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )

    return app


def _prepare_forward(
    config: CostwiseConfig,
    decision: RoutingDecision,
    request_body: dict,
    target_url: str,
    default_client: httpx.AsyncClient,
    get_client,
) -> tuple[httpx.AsyncClient, dict, str]:
    """Prepare client, body, and URL for forwarding based on routing decision."""
    client = default_client
    send_body = request_body
    send_url = target_url

    if decision.is_rerouted:
        send_body = dict(request_body)
        send_body["model"] = decision.routed_model

        source_fmt = detect_format(request_body)
        target_provider = decision.provider
        needs_translation = (
            (source_fmt == ApiFormat.ANTHROPIC and target_provider == "openai")
            or (source_fmt == ApiFormat.OPENAI and target_provider == "anthropic")
        )

        if needs_translation:
            if source_fmt == ApiFormat.ANTHROPIC and target_provider == "openai":
                send_body = anthropic_to_openai(send_body)
                send_body["model"] = decision.routed_model
                send_url = "/v1/chat/completions"
            elif source_fmt == ApiFormat.OPENAI and target_provider == "anthropic":
                send_body = openai_to_anthropic(send_body)
                send_body["model"] = decision.routed_model
                send_url = "/v1/messages"

        if decision.api_base and decision.api_base != config.proxy.upstream:
            client = get_client(decision.api_base)

    return client, send_body, send_url


async def _handle_streaming(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: bytes,
    decision: RoutingDecision,
    session_id: str,
    start: float,
    store: TrackingStore,
    health_tracker: ProviderHealthTracker | None = None,
    budget_enforcer: BudgetEnforcer | None = None,
    prune_result: PruneResult | None = None,
    content_hash: str | None = None,
    retry_event: RetryEvent | None = None,
    tuner: ThresholdTuner | None = None,
) -> StreamingResponse:
    async def stream_generator() -> AsyncIterator[bytes]:
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None

        async with client.stream("POST", url, headers=headers, content=body) as resp:
            status_code = resp.status_code
            async for line in resp.aiter_lines():
                yield f"{line}\n".encode()

                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        continue
                    p, c, t = _extract_stream_usage(data)
                    if p is not None:
                        prompt_tokens = p
                    if c is not None:
                        completion_tokens = c
                    if t is not None:
                        total_tokens = t

            latency_ms = (time.monotonic() - start) * 1000
            if total_tokens is None and prompt_tokens and completion_tokens:
                total_tokens = prompt_tokens + completion_tokens

            # Record provider health for streaming responses
            if health_tracker:
                if status_code == 429:
                    health_tracker.record_rate_limit(decision.provider, latency_ms)
                elif status_code >= 400:
                    health_tracker.record_error(decision.provider, latency_ms, status_code)
                else:
                    health_tracker.record_success(decision.provider, latency_ms, status_code)

            await store.record_provider_health(
                decision.provider, decision.routed_model,
                latency_ms, status_code,
                rate_limited=status_code == 429,
            )

            if budget_enforcer and decision.cost_estimate and status_code < 400:
                budget_enforcer.record_spend(decision.cost_estimate.estimated_total_cost)

            record = _build_record(
                decision, url, session_id,
                prompt_tokens, completion_tokens, total_tokens,
                latency_ms, status_code,
                prune_result=prune_result,
                content_hash=content_hash,
            )
            request_id = await store.record_request(record)

            if retry_event and retry_event.was_downgraded:
                await store.record_retry_event(
                    session_id=session_id,
                    original_request_id=retry_event.original_request_id,
                    retry_request_id=request_id,
                    content_hash=retry_event.content_hash,
                    similarity_score=retry_event.similarity_score,
                    original_tier=retry_event.original_tier,
                    original_model=retry_event.original_model,
                    time_delta_s=retry_event.time_delta_s,
                    was_downgraded=True,
                )
                if tuner:
                    await tuner.on_retry(retry_event)

    resp_headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if decision.is_rerouted:
        resp_headers["x-costwise-routed"] = decision.routed_model
        resp_headers["x-costwise-tier"] = decision.tier.value
    if prune_result and prune_result.dropped_entries > 0:
        resp_headers["x-costwise-pruned"] = str(prune_result.tokens_saved)
    if decision.budget_warning:
        resp_headers["x-costwise-budget-action"] = decision.budget_action
        resp_headers["x-costwise-budget-warning"] = decision.budget_warning

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers=resp_headers,
    )
