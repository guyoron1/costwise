"""Tests for provider health tracking (circuit breaker)."""

from __future__ import annotations

import time

import pytest

from costwise.core.health import ProviderHealthTracker, ProviderStatus


class TestProviderHealthTracker:

    def test_unknown_provider_is_healthy(self):
        tracker = ProviderHealthTracker()
        assert tracker.is_healthy("anthropic")
        assert tracker.get_status("anthropic") == ProviderStatus.HEALTHY

    def test_success_keeps_healthy(self):
        tracker = ProviderHealthTracker()
        tracker.record_success("anthropic", 150.0)
        tracker.record_success("anthropic", 200.0)
        assert tracker.is_healthy("anthropic")

    def test_rate_limit_makes_unhealthy(self):
        tracker = ProviderHealthTracker(rate_limit_cooldown_s=30.0)
        tracker.record_rate_limit("openai", 50.0)
        assert not tracker.is_healthy("openai")
        assert tracker.get_status("openai") == ProviderStatus.UNHEALTHY

    def test_rate_limit_cooldown_expires(self):
        tracker = ProviderHealthTracker(rate_limit_cooldown_s=0.1)
        tracker.record_rate_limit("openai", 50.0)
        assert not tracker.is_healthy("openai")
        time.sleep(0.15)
        assert tracker.is_healthy("openai")

    def test_consecutive_errors_make_unhealthy(self):
        tracker = ProviderHealthTracker(consecutive_error_limit=3)
        for _ in range(3):
            tracker.record_error("google", 100.0, 500, "internal error")
        assert tracker.get_status("google") == ProviderStatus.UNHEALTHY

    def test_success_resets_consecutive_errors(self):
        tracker = ProviderHealthTracker(
            consecutive_error_limit=3,
            error_rate_threshold=0.90,
            min_requests_for_health=10,
        )
        tracker.record_error("google", 100.0, 500)
        tracker.record_error("google", 100.0, 500)
        tracker.record_success("google", 100.0)
        tracker.record_error("google", 100.0, 500)
        assert tracker.is_healthy("google")

    def test_high_error_rate_makes_unhealthy(self):
        tracker = ProviderHealthTracker(
            error_rate_threshold=0.50,
            min_requests_for_health=4,
            consecutive_error_limit=100,
        )
        tracker.record_success("anthropic", 100.0)
        tracker.record_error("anthropic", 100.0, 500)
        tracker.record_error("anthropic", 100.0, 500)
        tracker.record_error("anthropic", 100.0, 500)
        assert tracker.get_status("anthropic") == ProviderStatus.UNHEALTHY

    def test_moderate_error_rate_degraded(self):
        tracker = ProviderHealthTracker(
            error_rate_threshold=0.50,
            min_requests_for_health=4,
            consecutive_error_limit=100,
        )
        tracker.record_success("anthropic", 100.0)
        tracker.record_success("anthropic", 100.0)
        tracker.record_success("anthropic", 100.0)
        tracker.record_error("anthropic", 100.0, 500)
        assert tracker.get_status("anthropic") == ProviderStatus.DEGRADED

    def test_healthy_providers_filters(self):
        tracker = ProviderHealthTracker(rate_limit_cooldown_s=30.0)
        tracker.record_rate_limit("openai")
        tracker.record_success("anthropic", 100.0)

        result = tracker.healthy_providers({"anthropic", "openai", "google"})
        assert "anthropic" in result
        assert "google" in result
        assert "openai" not in result

    def test_snapshot_captures_stats(self):
        tracker = ProviderHealthTracker()
        tracker.record_success("anthropic", 100.0)
        tracker.record_success("anthropic", 200.0)
        tracker.record_error("anthropic", 50.0, 500, "bad")

        snap = tracker.get_snapshot("anthropic")
        assert snap.total_requests == 3
        assert snap.error_count == 1
        assert snap.avg_latency_ms == pytest.approx(116.67, rel=0.01)

    def test_get_all_snapshots(self):
        tracker = ProviderHealthTracker()
        tracker.record_success("anthropic", 100.0)
        tracker.record_success("openai", 200.0)

        snaps = tracker.get_all_snapshots()
        assert "anthropic" in snaps
        assert "openai" in snaps

    def test_reset_single_provider(self):
        tracker = ProviderHealthTracker()
        tracker.record_rate_limit("openai")
        tracker.reset("openai")
        assert tracker.is_healthy("openai")

    def test_reset_all(self):
        tracker = ProviderHealthTracker()
        tracker.record_rate_limit("openai")
        tracker.record_rate_limit("google")
        tracker.reset()
        assert tracker.is_healthy("openai")
        assert tracker.is_healthy("google")

    def test_window_eviction(self):
        tracker = ProviderHealthTracker(
            window_seconds=0.1,
            consecutive_error_limit=100,
            error_rate_threshold=0.5,
            min_requests_for_health=2,
        )
        tracker.record_error("anthropic", 100.0, 500)
        tracker.record_error("anthropic", 100.0, 500)
        tracker.record_error("anthropic", 100.0, 500)
        time.sleep(0.15)
        tracker.record_success("anthropic", 100.0)
        tracker.record_success("anthropic", 100.0)
        assert tracker.is_healthy("anthropic")

    def test_min_requests_before_unhealthy(self):
        tracker = ProviderHealthTracker(
            min_requests_for_health=5,
            error_rate_threshold=0.5,
            consecutive_error_limit=100,
        )
        tracker.record_error("anthropic", 100.0, 500)
        tracker.record_error("anthropic", 100.0, 500)
        assert tracker.is_healthy("anthropic")
