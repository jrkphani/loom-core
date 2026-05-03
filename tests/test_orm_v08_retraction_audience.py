"""TDD: v0.8 retraction + audience profile ORM columns.

B5: Atom retraction columns; Stakeholder audience profile columns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Atom, Event, Stakeholder
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def _svc_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    command.upgrade(Config("alembic.ini"), "head")
    return db_path


@pytest_asyncio.fixture
async def svc_session(_svc_test_db: Path) -> AsyncIterator[AsyncSession]:
    engine = create_engine(_svc_test_db)
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


async def test_atom_has_retraction_columns(svc_session: AsyncSession) -> None:
    """Atom must have retracted, retracted_at, retraction_reason with CHECK on reason."""
    ev = Event(id=str(ULID()), domain="work", type="process", occurred_at=datetime.now(UTC))
    svc_session.add(ev)
    await svc_session.flush()

    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="decision",
        event_id=ev.id,
        content="Test atom",
        anchor_id="d-001",
    )
    svc_session.add(atom)
    await svc_session.flush()
    await svc_session.refresh(atom)

    # Defaults
    assert atom.retracted is False
    assert atom.retracted_at is None
    assert atom.retraction_reason is None

    # Round-trip a retraction
    now = datetime.now(UTC)
    atom.retracted = True
    atom.retracted_at = now
    atom.retraction_reason = "hallucination"
    await svc_session.flush()
    await svc_session.refresh(atom)

    assert atom.retracted is True
    assert atom.retracted_at is not None
    assert atom.retraction_reason == "hallucination"

    # CHECK on retraction_reason
    with pytest.raises(IntegrityError):
        bad = Atom(
            id=str(ULID()),
            domain="work",
            type="decision",
            event_id=ev.id,
            content="bad",
            anchor_id="d-bad",
            retracted=True,
            retraction_reason="invalid_reason",
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()


async def test_stakeholder_has_audience_profile(svc_session: AsyncSession) -> None:
    """Stakeholder must have 4 nullable audience columns with CHECK on schema."""
    sh = Stakeholder(
        id=str(ULID()),
        canonical_name="Madhavan R.",
        primary_email=f"madhavan-{ULID()}@example.com",
        audience_schema="executive",
        preferred_depth="summary",
        preferred_channel="email",
        tone_notes="formal, concise",
    )
    svc_session.add(sh)
    await svc_session.flush()
    await svc_session.refresh(sh)

    assert sh.audience_schema == "executive"
    assert sh.preferred_depth == "summary"
    assert sh.preferred_channel == "email"
    assert sh.tone_notes == "formal, concise"

    # All nullable
    sh2 = Stakeholder(
        id=str(ULID()),
        canonical_name="Anon",
        primary_email=f"anon-{ULID()}@example.com",
    )
    svc_session.add(sh2)
    await svc_session.flush()
    await svc_session.refresh(sh2)
    assert sh2.audience_schema is None
    assert sh2.preferred_depth is None

    # CHECK on audience_schema
    with pytest.raises(IntegrityError):
        bad = Stakeholder(
            id=str(ULID()),
            canonical_name="Bad",
            primary_email=f"bad-{ULID()}@example.com",
            audience_schema="invalid_schema",
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()
