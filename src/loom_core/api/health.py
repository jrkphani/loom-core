"""Health endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Health.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from loom_core import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response model for `GET /v1/health`."""

    status: str
    version: str
    uptime_seconds: float
    db_size_bytes: int | None = None


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Basic liveness check.

    Returns process status, version, uptime. The `db_size_bytes` field is
    populated once the database engine wires up (W1).
    """
    # Imported lazily to avoid a circular dependency: main.py imports this router.
    from loom_core.main import get_uptime_seconds

    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_seconds=get_uptime_seconds(),
        db_size_bytes=None,
    )
