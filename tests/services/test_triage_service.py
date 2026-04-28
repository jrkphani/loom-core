"""Service-level tests for the triage service."""

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
from loom_core.services.triage import create_triage_item
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
# T1 (DRIVER): create_triage_item happy path
# ---------------------------------------------------------------------------


async def test_create_triage_item_persists_with_generated_id_and_surfaced_at(
    svc_session: AsyncSession,
) -> None:
    """create_triage_item returns a persisted TriageItem with a generated ULID."""
    item = await create_triage_item(
        svc_session,
        item_type="ambiguous_routing",
        related_entity_type="file",
        related_entity_id="inbox/work/notes/foo.md",
        context_summary="Missing or invalid frontmatter.",
        priority_score=0.5,
    )

    assert len(item.id) == 26
    assert item.item_type == "ambiguous_routing"
    assert item.related_entity_id == "inbox/work/notes/foo.md"
    assert item.surfaced_at is not None
    assert item.resolved_at is None
    assert item.priority_score == 0.5
