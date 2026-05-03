"""TDD: v0.8 cross-cutting ORM columns — visibility_scope, retention_tier,
projection_at_creation — on Event, Atom, Hypothesis, Artifact, ArtifactVersion,
ExternalReference, Engagement, Arena.

Behaviours covered (added incrementally):
  B1: Event, Atom, Hypothesis
  B2: Artifact, ArtifactVersion, ExternalReference
  B3: Engagement, Arena
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import (
    Arena,
    Artifact,
    ArtifactVersion,
    Atom,
    Engagement,
    Event,
    ExternalReference,
    Hypothesis,
)
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


def _event_id() -> str:
    return str(ULID())


def _new_event(domain: str = "work") -> Event:
    return Event(
        id=_event_id(),
        domain=domain,
        type="process",
        occurred_at=datetime.now(UTC),
    )


def _new_atom(event_id: str, domain: str = "work") -> Atom:
    return Atom(
        id=str(ULID()),
        domain=domain,
        type="decision",
        event_id=event_id,
        content="Some decision",
        anchor_id="d-001",
    )


def _new_arena(domain: str = "work") -> Arena:
    return Arena(id=str(ULID()), domain=domain, name="Test Arena")


def _new_hypothesis(arena_id: str, domain: str = "work") -> Hypothesis:
    return Hypothesis(
        id=str(ULID()),
        domain=domain,
        arena_id=arena_id,
        layer="arena",
        title="Test Hypothesis",
    )


def _new_artifact(domain: str = "work") -> Artifact:
    return Artifact(id=str(ULID()), domain=domain, name="test notebook")


def _new_artifact_version(artifact_id: str) -> ArtifactVersion:
    return ArtifactVersion(
        id=str(ULID()),
        artifact_id=artifact_id,
        version_number=1,
        content_path="notebooks/work/test/versions/v_1.md",
    )


def _new_external_ref() -> ExternalReference:
    return ExternalReference(
        id=str(ULID()),
        ref_type="url",
        ref_value=f"https://example.com/{ULID()}",
    )


def _new_engagement(arena_id: str, domain: str = "work") -> Engagement:
    return Engagement(
        id=str(ULID()),
        domain=domain,
        arena_id=arena_id,
        name="Test Engagement",
    )


# ---------------------------------------------------------------------------
# B1: Event
# ---------------------------------------------------------------------------


async def test_event_has_v08_cross_cutting_columns(svc_session: AsyncSession) -> None:
    """Event must have visibility_scope, retention_tier, projection_at_creation."""
    ev = _new_event()
    svc_session.add(ev)
    await svc_session.flush()
    await svc_session.refresh(ev)

    # Default values
    assert ev.visibility_scope == "private"
    assert ev.retention_tier == "operational"
    assert ev.projection_at_creation == "work-cro-1cloudhub-v1"

    # Explicit valid value persists
    ev2 = _new_event()
    ev2.visibility_scope = "domain_wide"
    svc_session.add(ev2)
    await svc_session.flush()
    await svc_session.refresh(ev2)
    assert ev2.visibility_scope == "domain_wide"

    # Invalid value raises IntegrityError
    with pytest.raises(IntegrityError):
        await svc_session.execute(
            text(
                "INSERT INTO events (id, domain, type, occurred_at, visibility_scope)"
                " VALUES (:id, 'work', 'process', '2026-01-01', 'invalid')"
            ),
            {"id": str(ULID())},
        )


# ---------------------------------------------------------------------------
# B1: Atom
# ---------------------------------------------------------------------------


async def test_atom_has_v08_cross_cutting_columns(svc_session: AsyncSession) -> None:
    """Atom must have visibility_scope, retention_tier, projection_at_creation."""
    ev = _new_event()
    svc_session.add(ev)
    await svc_session.flush()

    atom = _new_atom(ev.id)
    svc_session.add(atom)
    await svc_session.flush()
    await svc_session.refresh(atom)

    assert atom.visibility_scope == "private"
    assert atom.retention_tier == "operational"
    assert atom.projection_at_creation == "work-cro-1cloudhub-v1"

    # Explicit valid
    atom2 = _new_atom(ev.id)
    atom2.anchor_id = "d-002"
    atom2.visibility_scope = "engagement_scoped"
    svc_session.add(atom2)
    await svc_session.flush()
    await svc_session.refresh(atom2)
    assert atom2.visibility_scope == "engagement_scoped"

    with pytest.raises(IntegrityError):
        await svc_session.execute(
            text(
                "INSERT INTO atoms (id, domain, type, event_id, content, anchor_id, visibility_scope)"
                " VALUES (:id, 'work', 'decision', :eid, 'x', 'd-999', 'bad')"
            ),
            {"id": str(ULID()), "eid": ev.id},
        )


# ---------------------------------------------------------------------------
# B1: Hypothesis
# ---------------------------------------------------------------------------


async def test_hypothesis_has_v08_cross_cutting_columns(svc_session: AsyncSession) -> None:
    """Hypothesis must have visibility_scope, retention_tier, projection_at_creation."""
    arena = _new_arena()
    svc_session.add(arena)
    await svc_session.flush()

    hyp = _new_hypothesis(arena.id)
    svc_session.add(hyp)
    await svc_session.flush()
    await svc_session.refresh(hyp)

    assert hyp.visibility_scope == "private"
    assert hyp.retention_tier == "operational"
    assert hyp.projection_at_creation == "work-cro-1cloudhub-v1"

    with pytest.raises(IntegrityError):
        await svc_session.execute(
            text(
                "INSERT INTO hypotheses"
                " (id, domain, arena_id, layer, title, visibility_scope)"
                " VALUES (:id, 'work', :aid, 'arena', 'H', 'bad')"
            ),
            {"id": str(ULID()), "aid": arena.id},
        )


# ---------------------------------------------------------------------------
# B2: Artifact
# ---------------------------------------------------------------------------


async def test_artifact_has_v08_cross_cutting_columns(svc_session: AsyncSession) -> None:
    """Artifact must have visibility_scope, retention_tier, projection_at_creation."""
    art = _new_artifact()
    svc_session.add(art)
    await svc_session.flush()
    await svc_session.refresh(art)

    assert art.visibility_scope == "private"
    assert art.retention_tier == "operational"
    assert art.projection_at_creation == "work-cro-1cloudhub-v1"


async def test_artifact_version_has_v08_cross_cutting_columns(
    svc_session: AsyncSession,
) -> None:
    """ArtifactVersion must have visibility_scope and retention_tier (no projection)."""
    art = _new_artifact()
    svc_session.add(art)
    await svc_session.flush()

    av = _new_artifact_version(art.id)
    svc_session.add(av)
    await svc_session.flush()
    await svc_session.refresh(av)

    assert av.visibility_scope == "private"
    assert av.retention_tier == "operational"
    assert not hasattr(av, "projection_at_creation")


async def test_external_reference_has_v08_cross_cutting_columns(
    svc_session: AsyncSession,
) -> None:
    """ExternalReference must have visibility_scope and retention_tier (no projection)."""
    er = _new_external_ref()
    svc_session.add(er)
    await svc_session.flush()
    await svc_session.refresh(er)

    assert er.visibility_scope == "private"
    assert er.retention_tier == "operational"
    assert not hasattr(er, "projection_at_creation")


# ---------------------------------------------------------------------------
# B3: Engagement
# ---------------------------------------------------------------------------


async def test_engagement_has_v08_cross_cutting_columns(svc_session: AsyncSession) -> None:
    """Engagement must have retention_tier and projection_at_creation (no visibility)."""
    arena = _new_arena()
    svc_session.add(arena)
    await svc_session.flush()

    eng = _new_engagement(arena.id)
    svc_session.add(eng)
    await svc_session.flush()
    await svc_session.refresh(eng)

    assert eng.retention_tier == "operational"
    assert eng.projection_at_creation == "work-cro-1cloudhub-v1"
    assert not hasattr(eng, "visibility_scope")


# ---------------------------------------------------------------------------
# B3: Arena
# ---------------------------------------------------------------------------


async def test_arena_has_v08_cross_cutting_columns(svc_session: AsyncSession) -> None:
    """Arena must have projection_at_creation only (no visibility, no retention)."""
    arena = _new_arena()
    svc_session.add(arena)
    await svc_session.flush()
    await svc_session.refresh(arena)

    assert arena.projection_at_creation == "work-cro-1cloudhub-v1"
    assert not hasattr(arena, "visibility_scope")
    assert not hasattr(arena, "retention_tier")
