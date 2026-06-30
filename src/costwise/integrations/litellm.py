"""LiteLLM callback adapter — track costs through LiteLLM without the proxy."""

from __future__ import annotations

import logging
from typing import Any

from costwise.tracking.store import RoutingRecord, TrackingStore

logger = logging.getLogger(__name__)


class CostwiseCallback:
    """LiteLLM callback that records requests to Costwise's tracking store.

    Usage:
        import litellm
        from costwise.integrations.litellm import CostwiseCallback
        from costwise.tracking.store import TrackingStore

        store = TrackingStore(db_path)
        litellm.callbacks = [CostwiseCallback(store)]
    """

    def __init__(self, store: TrackingStore, session_id: str = "litellm") -> None:
        self._store = store
        self._session_id = session_id

    async def async_success_handler(
        self,
        kwargs: dict[str, Any],
        response: Any,
        start_time: float | Any,
        end_time: float | Any,
    ) -> None:
        try:
            model = kwargs.get("model", "unknown")
            usage = getattr(response, "usage", None)

            prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
            total_tokens = getattr(usage, "total_tokens", None) if usage else None

            if total_tokens is None and prompt_tokens and completion_tokens:
                total_tokens = prompt_tokens + completion_tokens

            latency_ms = _compute_latency_ms(start_time, end_time)

            cost_usd = kwargs.get("response_cost")

            record = RoutingRecord(
                endpoint="litellm",
                request_model=model,
                session_id=self._session_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                provider=kwargs.get("litellm_params", {}).get("custom_llm_provider", "unknown"),
                status_code=200,
            )
            await self._store.record_request(record)
        except Exception:
            logger.warning("Failed to record LiteLLM success", exc_info=True)

    async def async_failure_handler(
        self,
        kwargs: dict[str, Any],
        exception: Any,
        start_time: float | Any,
        end_time: float | Any,
    ) -> None:
        try:
            model = kwargs.get("model", "unknown")
            latency_ms = _compute_latency_ms(start_time, end_time)
            status_code = getattr(exception, "status_code", 500)

            record = RoutingRecord(
                endpoint="litellm",
                request_model=model,
                session_id=self._session_id,
                latency_ms=latency_ms,
                provider=kwargs.get("litellm_params", {}).get("custom_llm_provider", "unknown"),
                status_code=status_code,
                error=str(exception)[:500],
            )
            await self._store.record_request(record)
        except Exception:
            logger.warning("Failed to record LiteLLM failure", exc_info=True)


def _compute_latency_ms(start_time: Any, end_time: Any) -> float:
    try:
        if isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)):
            return (end_time - start_time) * 1000
        if hasattr(start_time, "timestamp") and hasattr(end_time, "timestamp"):
            return (end_time.timestamp() - start_time.timestamp()) * 1000
    except (TypeError, AttributeError):
        pass
    return 0.0
