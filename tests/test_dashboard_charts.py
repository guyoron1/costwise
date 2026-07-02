"""Tests for SVG chart generators."""

from __future__ import annotations

from costwise.dashboard.charts import (
    budget_gauge,
    cost_bar_chart,
    model_donut_chart,
    savings_stacked_bars,
)


class TestCostBarChart:
    def test_valid_svg(self) -> None:
        data = [
            {"hour": "2024-01-01T10:00", "cost": 1.5, "saved": 0.5, "requests": 10},
            {"hour": "2024-01-01T11:00", "cost": 2.0, "saved": 1.0, "requests": 15},
        ]
        svg = cost_bar_chart(data)
        assert svg.strip().startswith("<svg")
        assert "</svg>" in svg

    def test_empty_data(self) -> None:
        svg = cost_bar_chart([])
        assert "<svg" in svg
        assert "No cost data" in svg

    def test_single_bar(self) -> None:
        data = [{"hour": "2024-01-01T12:00", "cost": 3.0, "saved": 1.0, "requests": 5}]
        svg = cost_bar_chart(data)
        assert "<rect" in svg

    def test_custom_dimensions(self) -> None:
        data = [{"hour": "2024-01-01T10:00", "cost": 1.0, "saved": 0.5, "requests": 3}]
        svg = cost_bar_chart(data, width=400, height=150)
        assert 'width="400"' in svg
        assert 'height="150"' in svg


class TestModelDonutChart:
    def test_valid_svg(self) -> None:
        data = [
            {"routed_model": "claude-haiku-3.5", "count": 50},
            {"routed_model": "claude-sonnet-4", "count": 30},
            {"routed_model": "claude-opus-4", "count": 20},
        ]
        svg = model_donut_chart(data)
        assert svg.strip().startswith("<svg")
        assert "<path" in svg

    def test_empty_data(self) -> None:
        svg = model_donut_chart([])
        assert "No model data" in svg

    def test_zero_counts(self) -> None:
        data = [{"routed_model": "test", "count": 0}]
        svg = model_donut_chart(data)
        assert "No requests" in svg

    def test_single_model(self) -> None:
        data = [{"routed_model": "claude-opus-4", "count": 100}]
        svg = model_donut_chart(data)
        assert "<path" in svg
        assert "100" in svg


class TestSavingsStackedBars:
    def test_valid_svg(self) -> None:
        breakdown = {"routing_saved_usd": 5.0, "total_tokens_pruned": 10000}
        svg = savings_stacked_bars(breakdown)
        assert svg.strip().startswith("<svg")
        assert "<rect" in svg

    def test_empty_breakdown(self) -> None:
        svg = savings_stacked_bars({})
        assert "No savings" in svg

    def test_zero_values(self) -> None:
        breakdown = {"routing_saved_usd": 0, "total_tokens_pruned": 0}
        svg = savings_stacked_bars(breakdown)
        assert "No savings" in svg


class TestBudgetGauge:
    def test_at_zero(self) -> None:
        svg = budget_gauge(0)
        assert svg.strip().startswith("<svg")
        assert "0%" in svg

    def test_at_50(self) -> None:
        svg = budget_gauge(50)
        assert "50%" in svg

    def test_at_100(self) -> None:
        svg = budget_gauge(100)
        assert "100%" in svg

    def test_clamped_above_100(self) -> None:
        svg = budget_gauge(150)
        assert "100%" in svg

    def test_custom_dimensions(self) -> None:
        svg = budget_gauge(75, width=300, height=180)
        assert 'width="300"' in svg
        assert 'height="180"' in svg
