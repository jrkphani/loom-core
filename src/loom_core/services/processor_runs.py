"""Processor runs service — lifecycle management for cron pipeline audit rows."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import ProcessorRun


async def start_processor_run(session: AsyncSession, *, pipeline: str) -> ProcessorRun:
    """Create a processor_runs row marking the start of a pipeline run.

    Returns:
        The newly created :class:`ProcessorRun` with started_at set.
        completed_at, items_processed, items_failed are all NULL at this point.
    """
    run = ProcessorRun(id=str(ULID()), pipeline=pipeline)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def finish_processor_run(
    session: AsyncSession,
    run_id: str,
    *,
    items_processed: int,
    items_failed: int,
    success: bool = True,
    notes: str | None = None,
) -> ProcessorRun | None:
    """Update a processor_runs row on completion.

    ``success`` defaults to True so happy-path callers can omit it.

    Returns:
        The updated :class:`ProcessorRun`, or None if run_id was not found.
    """
    run = await session.get(ProcessorRun, run_id)
    if run is None:
        return None
    run.completed_at = datetime.now(UTC)
    run.items_processed = items_processed
    run.items_failed = items_failed
    run.success = success
    run.notes = notes
    await session.flush()
    await session.refresh(run)
    return run


async def list_latest_runs_per_pipeline(
    session: AsyncSession,
) -> dict[str, ProcessorRun]:
    """Return the most recent ProcessorRun per distinct pipeline.

    Pipelines with no runs are absent from the result dict.
    """
    stmt = select(ProcessorRun.pipeline).distinct()
    pipelines = (await session.execute(stmt)).scalars().all()
    result: dict[str, ProcessorRun] = {}
    for p in pipelines:
        latest_stmt = (
            select(ProcessorRun)
            .where(ProcessorRun.pipeline == p)
            .order_by(ProcessorRun.started_at.desc())
            .limit(1)
        )
        run = (await session.execute(latest_stmt)).scalar_one()
        result[p] = run
    return result
