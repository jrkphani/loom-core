"""Health endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Health.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core import __version__
from loom_core.api._deps import get_session
from loom_core.services.processor_runs import list_latest_runs_per_pipeline

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


# ---------------------------------------------------------------------------
# GET /health/processor — last cron pipeline run per pipeline
# ---------------------------------------------------------------------------


class PipelineRunSummary(BaseModel):
    """Summary of the most recent run for one pipeline."""

    last_run_at: datetime
    completed_at: datetime | None
    items_processed: int | None
    items_failed: int | None


class HealthProcessorResponse(BaseModel):
    """Response model for GET /v1/health/processor."""

    pipelines: dict[str, PipelineRunSummary]


@router.get("/health/processor", response_model=HealthProcessorResponse)
async def get_health_processor(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthProcessorResponse:
    """Return the most recent processor_runs row per pipeline."""
    latest = await list_latest_runs_per_pipeline(session)
    return HealthProcessorResponse(
        pipelines={
            name: PipelineRunSummary(
                last_run_at=run.started_at,
                completed_at=run.completed_at,
                items_processed=run.items_processed,
                items_failed=run.items_failed,
            )
            for name, run in latest.items()
        }
    )
