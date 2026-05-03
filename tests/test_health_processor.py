"""Tests for GET /v1/health/processor endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import ProcessorRun


async def test_get_health_processor_returns_last_inbox_sweep_run(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /v1/health/processor returns the most recent run per pipeline."""
    now = datetime.now(UTC)
    earlier = now - timedelta(minutes=10)

    run_old = ProcessorRun(
        id=str(ULID()),
        pipeline="inbox_sweep",
        started_at=earlier,
        completed_at=earlier + timedelta(seconds=2),
        items_processed=2,
        items_failed=0,
    )
    run_new = ProcessorRun(
        id=str(ULID()),
        pipeline="inbox_sweep",
        started_at=now,
        completed_at=now + timedelta(seconds=3),
        items_processed=5,
        items_failed=1,
    )
    db_session.add(run_old)
    db_session.add(run_new)
    await db_session.commit()

    resp = await client.get("/v1/health/processor")
    assert resp.status_code == 200
    data = resp.json()

    assert "inbox_sweep" in data["pipelines"]
    summary = data["pipelines"]["inbox_sweep"]
    assert summary["items_processed"] == 5
    assert summary["items_failed"] == 1
    assert summary["success"] is True
    assert summary["completed_at"] is not None


async def test_get_health_processor_returns_empty_pipelines_when_no_runs(
    client: AsyncClient,
) -> None:
    """Confirmation: GET /v1/health/processor returns empty dict with no rows."""
    resp = await client.get("/v1/health/processor")
    assert resp.status_code == 200
    assert resp.json() == {"pipelines": {}}
