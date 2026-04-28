"""Service-level tests for the processor_runs service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.services.processor_runs import finish_processor_run, start_processor_run
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def _svc_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    return db_path


@pytest_asyncio.fixture
async def svc_session(_svc_test_db: Path) -> AsyncIterator[AsyncSession]:
    """Bare async session for service-level tests (no HTTP client needed)."""
    engine = create_engine(_svc_test_db)
    factory = create_session_factory(engine)

    async def _override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    app.state.session_factory = factory

    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides.pop(get_session, None)
    app.state.session_factory = None
    await engine.dispose()


# ---------------------------------------------------------------------------
# T1 (DRIVER): start_processor_run creates row with started_at
# ---------------------------------------------------------------------------


async def test_start_processor_run_creates_row_with_started_at(
    svc_session: AsyncSession,
) -> None:
    """start_processor_run returns a ProcessorRun with started_at set, completed_at None."""
    run = await start_processor_run(svc_session, pipeline="inbox_sweep")

    assert len(run.id) == 26
    assert run.pipeline == "inbox_sweep"
    assert run.started_at is not None
    assert run.completed_at is None
    assert run.items_processed is None
    assert run.items_failed is None


# ---------------------------------------------------------------------------
# T2 (DRIVER): finish_processor_run updates completion fields
# ---------------------------------------------------------------------------


async def test_finish_processor_run_updates_completion_fields(
    svc_session: AsyncSession,
) -> None:
    """finish_processor_run sets completed_at, items_processed, items_failed."""
    run = await start_processor_run(svc_session, pipeline="inbox_sweep")
    run_id = run.id
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        finished = await finish_processor_run(session2, run_id, items_processed=3, items_failed=1)
        await session2.commit()

    assert finished is not None
    assert finished.id == run_id
    assert finished.completed_at is not None
    assert finished.items_processed == 3
    assert finished.items_failed == 1
