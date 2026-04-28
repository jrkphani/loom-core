"""Lifespan smoke test — verifies APScheduler is registered on app startup."""

from __future__ import annotations

from pathlib import Path

from apscheduler.triggers.interval import IntervalTrigger

from loom_core.main import app, lifespan


async def test_lifespan_registers_inbox_sweep_job(_test_db: Path) -> None:
    """lifespan registers an inbox_sweep job with IntervalTrigger(minutes=5)."""
    async with lifespan(app):
        assert hasattr(app.state, "scheduler")
        assert app.state.scheduler is not None

        jobs = app.state.scheduler.get_jobs()
        ids = [j.id for j in jobs]
        assert "inbox_sweep" in ids

        job = next(j for j in jobs if j.id == "inbox_sweep")
        assert isinstance(job.trigger, IntervalTrigger)
        assert int(job.trigger.interval.total_seconds()) == 300
