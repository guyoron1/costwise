"""Tests for the Vertex AI adapter."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from costwise.proxy.vertex import build_vertex_url, prepare_vertex_headers, vertex_base_url


class TestBuildVertexUrl:
    def test_non_streaming(self):
        url = build_vertex_url("us-east5", "my-project", "claude-sonnet-4-6", streaming=False)
        assert url == (
            "/v1beta1/projects/my-project/locations/us-east5"
            "/publishers/anthropic/models/claude-sonnet-4-6:rawPredict"
        )

    def test_streaming(self):
        url = build_vertex_url("us-east5", "my-project", "claude-sonnet-4-6", streaming=True)
        assert url.endswith(":streamRawPredict")
        assert "claude-sonnet-4-6" in url

    def test_different_region(self):
        url = build_vertex_url("europe-west4", "eu-proj", "claude-haiku-4-5", streaming=False)
        assert "/locations/europe-west4/" in url
        assert "/projects/eu-proj/" in url

    def test_opus_model(self):
        url = build_vertex_url("us-east5", "proj", "claude-opus-4-7", streaming=True)
        assert "claude-opus-4-7:streamRawPredict" in url


class TestVertexBaseUrl:
    def test_us_east5(self):
        assert vertex_base_url("us-east5") == "https://us-east5-aiplatform.googleapis.com"

    def test_europe_west4(self):
        assert vertex_base_url("europe-west4") == "https://europe-west4-aiplatform.googleapis.com"


class TestPrepareVertexHeaders:
    def test_has_bearer_token(self):
        headers = prepare_vertex_headers("ya29.fake-token")
        assert headers["Authorization"] == "Bearer ya29.fake-token"
        assert headers["Content-Type"] == "application/json"

    def test_no_api_key(self):
        headers = prepare_vertex_headers("token")
        assert "x-api-key" not in headers
        assert "anthropic-version" not in headers


class TestVertexConfig:
    def test_auto_enable_from_env(self):
        with patch.dict(os.environ, {
            "ANTHROPIC_VERTEX_PROJECT_ID": "test-project",
            "CLOUD_ML_REGION": "us-central1",
        }):
            from costwise.config.schema import VertexConfig
            cfg = VertexConfig()
            assert cfg.enabled is True
            assert cfg.project_id == "test-project"
            assert cfg.region == "us-central1"

    def test_disabled_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("ANTHROPIC_VERTEX_PROJECT_ID", None)
            env.pop("CLOUD_ML_REGION", None)
            with patch.dict(os.environ, env, clear=True):
                from costwise.config.schema import VertexConfig
                cfg = VertexConfig(project_id="", region="")
                assert cfg.enabled is False

    def test_explicit_config(self):
        with patch.dict(os.environ, {}, clear=True):
            env = os.environ.copy()
            env.pop("ANTHROPIC_VERTEX_PROJECT_ID", None)
            env.pop("CLOUD_ML_REGION", None)
            with patch.dict(os.environ, env, clear=True):
                from costwise.config.schema import VertexConfig
                cfg = VertexConfig(
                    enabled=True,
                    project_id="explicit-proj",
                    region="asia-southeast1",
                )
                assert cfg.enabled is True
                assert cfg.project_id == "explicit-proj"
                assert cfg.region == "asia-southeast1"

    def test_nested_in_proxy_config(self):
        with patch.dict(os.environ, {
            "ANTHROPIC_VERTEX_PROJECT_ID": "nested-test",
            "CLOUD_ML_REGION": "us-east5",
        }):
            from costwise.config.schema import ProxyConfig
            proxy = ProxyConfig()
            assert proxy.vertex.enabled is True
            assert proxy.vertex.project_id == "nested-test"


class TestVertexAuthProvider:
    def _make_auth(self, mock_creds):
        """Create a VertexAuthProvider with mocked credentials."""
        import google.auth.transport.requests as gat_requests

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch.object(gat_requests, "Request"):
            from costwise.proxy.vertex import VertexAuthProvider
            return VertexAuthProvider()

    def test_get_token_refreshes_when_expired(self):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.token = "refreshed-token"

        auth = self._make_auth(mock_creds)
        token = auth.get_token()

        mock_creds.refresh.assert_called_once()
        assert token == "refreshed-token"

    def test_get_token_cached_when_valid(self):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "cached-token"

        auth = self._make_auth(mock_creds)
        token = auth.get_token()

        mock_creds.refresh.assert_not_called()
        assert token == "cached-token"
