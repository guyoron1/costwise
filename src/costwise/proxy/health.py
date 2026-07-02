"""Health check endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])

_ready = False


def set_ready(ready: bool) -> None:
    global _ready
    _ready = ready


@router.api_route("/", methods=["GET", "HEAD"])
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "costwise"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "costwise"}


@router.get("/ready")
async def ready() -> dict[str, str | bool]:
    return {"ready": _ready, "service": "costwise"}
