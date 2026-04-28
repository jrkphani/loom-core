"""Service-level tests for the external_references service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.services.external_references import (
    AtomNotFoundError,
    ExternalReferenceNotFoundError,
    create_external_reference,
    link_atom_to_external_ref,
)
from loom_core.storage.models import Atom, AtomExternalRef, Event
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
# Test helpers
# ---------------------------------------------------------------------------


async def _insert_event(session: AsyncSession, *, event_type: str = "process") -> str:
    """Insert a minimal valid Event and return its ID."""
    from ulid import ULID

    ev = Event(
        id=str(ULID()),
        domain="work",
        type=event_type,
        occurred_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )
    session.add(ev)
    await session.flush()
    return ev.id


async def _insert_atom(
    session: AsyncSession,
    *,
    event_id: str,
    atom_type: str = "decision",
    anchor_id: str = "d-001",
) -> str:
    """Insert a minimal valid Atom and return its ID."""
    from ulid import ULID

    atom = Atom(
        id=str(ULID()),
        domain="work",
        type=atom_type,
        event_id=event_id,
        content="Test content",
        anchor_id=anchor_id,
    )
    session.add(atom)
    await session.flush()
    return atom.id


# ---------------------------------------------------------------------------
# B1 (DRIVER): create_external_reference happy path
# ---------------------------------------------------------------------------


async def test_create_external_reference_persists_with_generated_id_returns_created_true(
    svc_session: AsyncSession,
) -> None:
    """create_external_reference returns (ref, True) with a generated ULID and captured_at."""
    ref, created = await create_external_reference(
        svc_session,
        ref_type="url",
        ref_value="https://example.com/post",
        summary_md_path="outbox/work/external/foo.md",
    )

    assert created is True
    assert len(ref.id) == 26
    assert ref.ref_type == "url"
    assert ref.ref_value == "https://example.com/post"
    assert ref.summary_md_path == "outbox/work/external/foo.md"
    assert ref.captured_at is not None
    assert ref.unreachable is False


# ---------------------------------------------------------------------------
# B2 (DRIVER): duplicate returns existing row with created=False
# ---------------------------------------------------------------------------


async def test_create_external_reference_duplicate_returns_existing_with_created_false(
    svc_session: AsyncSession,
) -> None:
    """Second call with same (ref_type, ref_value) returns the existing row and created=False."""
    ref1, _ = await create_external_reference(
        svc_session,
        ref_type="url",
        ref_value="https://x.com",
    )
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        ref2, created = await create_external_reference(
            session2,
            ref_type="url",
            ref_value="https://x.com",
            summary_md_path="other/path.md",
        )
        await session2.commit()

    assert created is False
    assert ref2.id == ref1.id


# ---------------------------------------------------------------------------
# B3 (DRIVER): link_atom_to_external_ref happy path
# ---------------------------------------------------------------------------


async def test_link_atom_to_external_ref_creates_junction_returns_created_true(
    svc_session: AsyncSession,
) -> None:
    """link_atom_to_external_ref creates a junction row and returns created=True."""
    event_id = await _insert_event(svc_session)
    atom_id = await _insert_atom(svc_session, event_id=event_id)
    ref, _ = await create_external_reference(
        svc_session, ref_type="url", ref_value="https://example.com"
    )
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        junction, created = await link_atom_to_external_ref(
            session2, atom_id=atom_id, external_ref_id=ref.id
        )
        await session2.commit()

    assert created is True
    assert junction.atom_id == atom_id
    assert junction.external_ref_id == ref.id

    async with app.state.session_factory() as session3:
        rows = (
            (
                await session3.execute(
                    select(AtomExternalRef).where(AtomExternalRef.atom_id == atom_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# B4 (DRIVER): link raises AtomNotFoundError when atom doesn't exist
# ---------------------------------------------------------------------------


async def test_link_atom_to_external_ref_raises_atom_not_found_for_missing_atom(
    svc_session: AsyncSession,
) -> None:
    """link_atom_to_external_ref raises AtomNotFoundError for a non-existent atom_id."""
    from ulid import ULID

    ref, _ = await create_external_reference(
        svc_session, ref_type="url", ref_value="https://example.com"
    )
    await svc_session.commit()

    bogus_atom_id = str(ULID())
    async with app.state.session_factory() as session2:
        with pytest.raises(AtomNotFoundError):
            await link_atom_to_external_ref(session2, atom_id=bogus_atom_id, external_ref_id=ref.id)
        await session2.rollback()


# ---------------------------------------------------------------------------
# B5 (DRIVER): link raises ExternalReferenceNotFoundError for missing ref
# ---------------------------------------------------------------------------


async def test_link_atom_to_external_ref_raises_ref_not_found_for_missing_ref(
    svc_session: AsyncSession,
) -> None:
    """link_atom_to_external_ref raises ExternalReferenceNotFoundError for a bogus ref id."""
    from ulid import ULID

    event_id = await _insert_event(svc_session)
    atom_id = await _insert_atom(svc_session, event_id=event_id)
    await svc_session.commit()

    bogus_ref_id = str(ULID())
    async with app.state.session_factory() as session2:
        with pytest.raises(ExternalReferenceNotFoundError):
            await link_atom_to_external_ref(session2, atom_id=atom_id, external_ref_id=bogus_ref_id)
        await session2.rollback()


# ---------------------------------------------------------------------------
# B6 (DRIVER): duplicate link returns existing junction with created=False
# ---------------------------------------------------------------------------


async def test_link_atom_to_external_ref_duplicate_returns_existing_with_created_false(
    svc_session: AsyncSession,
) -> None:
    """Second link call with same (atom_id, external_ref_id) returns existing and created=False."""
    event_id = await _insert_event(svc_session)
    atom_id = await _insert_atom(svc_session, event_id=event_id)
    ref, _ = await create_external_reference(
        svc_session, ref_type="url", ref_value="https://example.com"
    )
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        j1, _ = await link_atom_to_external_ref(session2, atom_id=atom_id, external_ref_id=ref.id)
        await session2.commit()

    async with app.state.session_factory() as session3:
        j2, created = await link_atom_to_external_ref(
            session3, atom_id=atom_id, external_ref_id=ref.id
        )
        await session3.commit()

    assert created is False
    assert j2.atom_id == j1.atom_id
    assert j2.external_ref_id == j1.external_ref_id

    async with app.state.session_factory() as session4:
        rows = (
            (
                await session4.execute(
                    select(AtomExternalRef).where(AtomExternalRef.atom_id == atom_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
