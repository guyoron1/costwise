"""Google Vertex AI adapter — URL construction, OAuth2 auth, header preparation."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

try:
    import google.auth
    import google.auth.transport.requests

    _GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    _GOOGLE_AUTH_AVAILABLE = False


_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def is_available() -> bool:
    return _GOOGLE_AUTH_AVAILABLE


def build_vertex_url(
    region: str,
    project_id: str,
    model: str,
    streaming: bool,
) -> str:
    suffix = "streamRawPredict" if streaming else "rawPredict"
    return (
        f"/v1beta1/projects/{project_id}/locations/{region}"
        f"/publishers/anthropic/models/{model}:{suffix}"
    )


def vertex_base_url(region: str) -> str:
    return f"https://{region}-aiplatform.googleapis.com"


def prepare_vertex_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


class VertexAuthProvider:
    """Manages Google OAuth2 credentials for Vertex AI.

    Tokens are cached and auto-refreshed when expired (~1 hour lifetime).
    Thread-safe via a lock around refresh operations.
    """

    def __init__(self) -> None:
        if not _GOOGLE_AUTH_AVAILABLE:
            raise ImportError(
                "google-auth is required for Vertex AI support. "
                "Install it with: pip install costwise[vertex]"
            )
        self._lock = threading.Lock()
        self._credentials, self._project = google.auth.default(scopes=_VERTEX_SCOPES)
        self._request = google.auth.transport.requests.Request()

    def get_token(self) -> str:
        with self._lock:
            if not self._credentials.valid:
                self._credentials.refresh(self._request)
            return self._credentials.token

    @property
    def project(self) -> str | None:
        return self._project
