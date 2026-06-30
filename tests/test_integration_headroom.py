"""Tests for Headroom integration — hooks and compression wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from costwise.integrations.headroom import (
    CompressionResult,
    CostwiseCompressionHooks,
    compress_messages,
    is_available,
)


class TestCostwiseCompressionHooks:
    def test_no_scores_returns_empty_biases(self):
        hooks = CostwiseCompressionHooks()
        messages = [{"role": "user", "content": "hello"}]
        biases = hooks.compute_biases(messages, None)
        assert biases == {}

    def test_relevance_scores_produce_biases(self):
        scores = {0: 0.8, 1: 0.2, 2: 1.0}
        hooks = CostwiseCompressionHooks(relevance_scores=scores)
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        biases = hooks.compute_biases(messages, None)
        assert 0 in biases
        assert 1 in biases
        assert 2 in biases
        assert biases[2] > biases[0] > biases[1]

    def test_out_of_range_indices_skipped(self):
        scores = {0: 0.5, 99: 0.9}
        hooks = CostwiseCompressionHooks(relevance_scores=scores)
        messages = [{"role": "user", "content": "a"}]
        biases = hooks.compute_biases(messages, None)
        assert 0 in biases
        assert 99 not in biases

    def test_high_relevance_gets_higher_bias(self):
        hooks = CostwiseCompressionHooks({0: 1.0, 1: 0.0})
        messages = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
        biases = hooks.compute_biases(messages, None)
        assert biases[0] > biases[1]

    def test_post_compress_stores_event(self):
        hooks = CostwiseCompressionHooks()
        assert hooks.last_event is None
        fake_event = MagicMock()
        hooks.post_compress(fake_event)
        assert hooks.last_event is fake_event


class TestCompressMessages:
    def test_passthrough_when_not_available(self):
        with patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", False):
            result = compress_messages(
                [{"role": "user", "content": "hello world"}],
                model="claude-sonnet-4-5-20250929",
            )
            assert result.applied is False
            assert result.messages == [{"role": "user", "content": "hello world"}]
            assert result.tokens_saved == 0

    def test_result_is_compression_result(self):
        with patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", False):
            result = compress_messages(
                [{"role": "user", "content": "test"}],
                model="test-model",
            )
            assert isinstance(result, CompressionResult)

    def test_passthrough_tokens_estimated(self):
        content = "a" * 400
        with patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", False):
            result = compress_messages(
                [{"role": "user", "content": content}],
                model="test",
            )
            assert result.tokens_before == 100
            assert result.tokens_after == 100

    def test_with_headroom_calls_compress(self):
        mock_result = MagicMock()
        mock_result.messages = [{"role": "user", "content": "compressed"}]
        mock_result.tokens_before = 1000
        mock_result.tokens_after = 400
        mock_result.tokens_saved = 600
        mock_result.compression_ratio = 0.6

        with (
            patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", True),
            patch("costwise.integrations.headroom._headroom_compress", mock_result, create=True) as mock_fn,
        ):
            mock_fn.return_value = mock_result
            with patch("costwise.integrations.headroom._headroom_compress", mock_fn):
                result = compress_messages(
                    [{"role": "user", "content": "big content"}],
                    model="claude-sonnet-4-5-20250929",
                    relevance_scores={0: 0.9},
                )
                assert result.applied is True
                assert result.tokens_saved == 600

    def test_exception_falls_back_to_passthrough(self):
        with (
            patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", True),
            patch("costwise.integrations.headroom._headroom_compress", side_effect=RuntimeError("boom"), create=True),
        ):
            result = compress_messages(
                [{"role": "user", "content": "test"}],
                model="test",
            )
            assert result.applied is False
            assert result.tokens_saved == 0


class TestIsAvailable:
    def test_reflects_import_state(self):
        with patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", True):
            assert is_available() is True
        with patch("costwise.integrations.headroom._HEADROOM_AVAILABLE", False):
            assert is_available() is False
