"""Integration tests for the inbox_sweep pipeline job."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import frontmatter
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.pipelines.inbox_sweep import inbox_sweep_job
from loom_core.storage.models import Event, ProcessorRun, TriageItem
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """A minimal vault directory structure under tmp_path."""
    for subdir in ("transcripts", "dictation", "emails", "notes"):
        (tmp_path / "inbox" / "work" / subdir).mkdir(parents=True)
    return tmp_path


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
    """Bare async session backed by the test DB."""
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
# B1 (DRIVER): happy path — 3 files across subdirs → 3 events, 1 processor run
# ---------------------------------------------------------------------------


async def test_inbox_sweep_job_creates_events_for_high_confidence_files(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """inbox_sweep_job processes 3 high-confidence files: 3 events, 1 processor run."""
    (tmp_vault / "inbox" / "work" / "transcripts" / "a.vtt").write_text(
        "WEBVTT\n\nHello from the transcript.", encoding="utf-8"
    )
    (tmp_vault / "inbox" / "work" / "dictation" / "b.txt").write_text(
        "Quick dictation note.", encoding="utf-8"
    )
    note_file = tmp_vault / "inbox" / "work" / "notes" / "c.md"
    post = frontmatter.Post("Meeting notes.", **{"type": "note"})
    note_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    await inbox_sweep_job(
        session_factory=app.state.session_factory,
        vault_path=tmp_vault,
    )

    events = (await svc_session.execute(select(Event))).scalars().all()
    assert len(events) == 3

    triage = (await svc_session.execute(select(TriageItem))).scalars().all()
    assert len(triage) == 0

    runs = (await svc_session.execute(select(ProcessorRun))).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert run.pipeline == "inbox_sweep"
    assert run.completed_at is not None
    assert run.items_processed == 3
    assert run.items_failed == 0


# ---------------------------------------------------------------------------
# B2 (CONFIRM): no files → processor run with items_processed=0
# ---------------------------------------------------------------------------


async def test_inbox_sweep_job_no_files_records_zero_processed(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """Confirmation: empty inbox dirs yield items_processed=0, items_failed=0."""
    await inbox_sweep_job(
        session_factory=app.state.session_factory,
        vault_path=tmp_vault,
    )

    events = (await svc_session.execute(select(Event))).scalars().all()
    assert len(events) == 0

    runs = (await svc_session.execute(select(ProcessorRun))).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert run.completed_at is not None
    assert run.items_processed == 0
    assert run.items_failed == 0


# ---------------------------------------------------------------------------
# B3 (DRIVER): per-file failure → items_failed incremented, sweep continues
# ---------------------------------------------------------------------------


async def test_inbox_sweep_job_per_file_failure_continues_and_records_failed(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """Per-file exception increments items_failed; sweep finishes with completed_at."""
    (tmp_vault / "inbox" / "work" / "transcripts" / "good.vtt").write_text(
        "WEBVTT\n\nGood file.", encoding="utf-8"
    )
    # Malformed YAML — colons in value position reliably cause parse errors.
    (tmp_vault / "inbox" / "work" / "notes" / "malformed.md").write_text(
        "---\ntype: note\nbroken yaml: : :\n---\n\nbody", encoding="utf-8"
    )

    await inbox_sweep_job(
        session_factory=app.state.session_factory,
        vault_path=tmp_vault,
    )

    events = (await svc_session.execute(select(Event))).scalars().all()
    assert len(events) == 1

    runs = (await svc_session.execute(select(ProcessorRun))).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert run.completed_at is not None
    assert run.items_processed == 1
    assert run.items_failed == 1


# ---------------------------------------------------------------------------
# B4 (CONFIRM): second sweep on same files → items_processed=0 (dedup)
# ---------------------------------------------------------------------------


async def test_inbox_sweep_job_does_not_count_duplicates(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """Confirmation: second sweep with unchanged files yields 0 processed."""
    (tmp_vault / "inbox" / "work" / "transcripts" / "a.vtt").write_text(
        "WEBVTT\n\nHello.", encoding="utf-8"
    )

    await inbox_sweep_job(
        session_factory=app.state.session_factory,
        vault_path=tmp_vault,
    )
    await inbox_sweep_job(
        session_factory=app.state.session_factory,
        vault_path=tmp_vault,
    )

    runs = (await svc_session.execute(select(ProcessorRun))).scalars().all()
    assert len(runs) == 2
    second_run = sorted(runs, key=lambda r: r.started_at)[-1]
    assert second_run.items_processed == 0
    assert second_run.items_failed == 0

    events = (await svc_session.execute(select(Event))).scalars().all()
    assert len(events) == 1
