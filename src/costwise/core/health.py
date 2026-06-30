"""Provider health tracking with circuit-breaker semantics.

Tracks rate limits, errors, and latency per provider in a sliding window.
Used by arbitrage to skip unhealthy providers and select fallbacks.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class HealthEvent:
    timestamp: float
    latency_ms: float
    status_code: int
    rate_limited: bool
    error: str | None = None


@dataclass
class ProviderHealthSnapshot:
    """Point-in-time health summary for a provider."""

    provider: str
    status: ProviderStatus
    total_requests: int = 0
    error_count: int = 0
    rate_limit_count: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    error_rate: float = 0.0
    last_rate_limit_at: float | None = None
    cooldown_remaining_s: float = 0.0


@dataclass
class _ProviderWindow:
    events: deque[HealthEvent] = field(default_factory=deque)
    last_rate_limit: float = 0.0
    consecutive_errors: int = 0


class ProviderHealthTracker:
    """In-memory sliding-window health tracker.

    Providers are marked unhealthy when:
    - Rate limited within cooldown period (default 30s)
    - Error rate exceeds threshold (default 50%) in the window
    - Consecutive errors exceed limit (default 5)

    Providers are marked degraded when:
    - Error rate exceeds half the threshold
    - Average latency exceeds the latency threshold
    """

    def __init__(
        self,
        *,
        window_seconds: float = 300.0,
        rate_limit_cooldown_s: float = 30.0,
        error_rate_threshold: float = 0.50,
        consecutive_error_limit: int = 5,
        latency_threshold_ms: float = 30_000.0,
        min_requests_for_health: int = 3,
    ) -> None:
        self._window_s = window_seconds
        self._cooldown_s = rate_limit_cooldown_s
        self._error_threshold = error_rate_threshold
        self._consecutive_limit = consecutive_error_limit
        self._latency_threshold = latency_threshold_ms
        self._min_requests = min_requests_for_health
        self._providers: dict[str, _ProviderWindow] = {}
        self._lock = Lock()

    def record_success(
        self, provider: str, latency_ms: float, status_code: int = 200
    ) -> None:
        with self._lock:
            pw = self._ensure_provider(provider)
            pw.events.append(
                HealthEvent(
                    timestamp=time.monotonic(),
                    latency_ms=latency_ms,
                    status_code=status_code,
                    rate_limited=False,
                )
            )
            pw.consecutive_errors = 0
            self._evict(pw)

    def record_error(
        self, provider: str, latency_ms: float, status_code: int, error: str = ""
    ) -> None:
        with self._lock:
            pw = self._ensure_provider(provider)
            pw.events.append(
                HealthEvent(
                    timestamp=time.monotonic(),
                    latency_ms=latency_ms,
                    status_code=status_code,
                    rate_limited=False,
                    error=error,
                )
            )
            pw.consecutive_errors += 1
            self._evict(pw)

    def record_rate_limit(
        self, provider: str, latency_ms: float = 0.0
    ) -> None:
        with self._lock:
            pw = self._ensure_provider(provider)
            now = time.monotonic()
            pw.events.append(
                HealthEvent(
                    timestamp=now,
                    latency_ms=latency_ms,
                    status_code=429,
                    rate_limited=True,
                )
            )
            pw.last_rate_limit = now
            pw.consecutive_errors += 1
            self._evict(pw)

    def is_healthy(self, provider: str) -> bool:
        return self.get_status(provider) == ProviderStatus.HEALTHY

    def get_status(self, provider: str) -> ProviderStatus:
        with self._lock:
            pw = self._providers.get(provider)
            if pw is None:
                return ProviderStatus.HEALTHY
            self._evict(pw)
            return self._compute_status(pw)

    def get_snapshot(self, provider: str) -> ProviderHealthSnapshot:
        with self._lock:
            pw = self._providers.get(provider)
            if pw is None:
                return ProviderHealthSnapshot(
                    provider=provider, status=ProviderStatus.HEALTHY
                )
            self._evict(pw)
            return self._build_snapshot(provider, pw)

    def get_all_snapshots(self) -> dict[str, ProviderHealthSnapshot]:
        with self._lock:
            result = {}
            for provider, pw in self._providers.items():
                self._evict(pw)
                result[provider] = self._build_snapshot(provider, pw)
            return result

    def healthy_providers(self, candidates: set[str]) -> set[str]:
        """Filter a set of providers down to only healthy ones."""
        with self._lock:
            result = set()
            for p in candidates:
                pw = self._providers.get(p)
                if pw is None:
                    result.add(p)
                    continue
                self._evict(pw)
                if self._compute_status(pw) != ProviderStatus.UNHEALTHY:
                    result.add(p)
            return result

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider:
                self._providers.pop(provider, None)
            else:
                self._providers.clear()

    def _ensure_provider(self, provider: str) -> _ProviderWindow:
        if provider not in self._providers:
            self._providers[provider] = _ProviderWindow()
        return self._providers[provider]

    def _evict(self, pw: _ProviderWindow) -> None:
        cutoff = time.monotonic() - self._window_s
        while pw.events and pw.events[0].timestamp < cutoff:
            pw.events.popleft()

    def _compute_status(self, pw: _ProviderWindow) -> ProviderStatus:
        now = time.monotonic()

        if pw.last_rate_limit and (now - pw.last_rate_limit) < self._cooldown_s:
            return ProviderStatus.UNHEALTHY

        if pw.consecutive_errors >= self._consecutive_limit:
            return ProviderStatus.UNHEALTHY

        if len(pw.events) < self._min_requests:
            return ProviderStatus.HEALTHY

        errors = sum(1 for e in pw.events if e.status_code >= 400 or e.rate_limited)
        error_rate = errors / len(pw.events)

        if error_rate >= self._error_threshold:
            return ProviderStatus.UNHEALTHY

        if error_rate >= self._error_threshold / 2:
            return ProviderStatus.DEGRADED

        latencies = [e.latency_ms for e in pw.events if not e.rate_limited]
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            if avg_latency > self._latency_threshold:
                return ProviderStatus.DEGRADED

        return ProviderStatus.HEALTHY

    def _build_snapshot(
        self, provider: str, pw: _ProviderWindow
    ) -> ProviderHealthSnapshot:
        events = list(pw.events)
        total = len(events)

        if total == 0:
            return ProviderHealthSnapshot(
                provider=provider, status=ProviderStatus.HEALTHY
            )

        errors = sum(1 for e in events if e.status_code >= 400 or e.rate_limited)
        rate_limits = sum(1 for e in events if e.rate_limited)
        latencies = sorted(e.latency_ms for e in events if not e.rate_limited)

        avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
        p95_lat = latencies[int(len(latencies) * 0.95)] if latencies else 0.0

        now = time.monotonic()
        cooldown_remaining = 0.0
        if pw.last_rate_limit:
            remaining = self._cooldown_s - (now - pw.last_rate_limit)
            cooldown_remaining = max(0.0, remaining)

        return ProviderHealthSnapshot(
            provider=provider,
            status=self._compute_status(pw),
            total_requests=total,
            error_count=errors,
            rate_limit_count=rate_limits,
            avg_latency_ms=avg_lat,
            p95_latency_ms=p95_lat,
            error_rate=errors / total if total else 0.0,
            last_rate_limit_at=pw.last_rate_limit or None,
            cooldown_remaining_s=cooldown_remaining,
        )
