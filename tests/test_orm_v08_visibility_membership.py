"""TDD: v0.8 new ORM classes — EntityVisibilityMember, StakeholderRole, AtomContribution.

B6 covers the three new tables added in migration §1.1, §1.5, §1.7.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import (
    Atom,
    AtomContribution,
    EntityVisibilityMember,
    Event,
    Stakeholder,
    StakeholderRole,
)
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


async def test_entity_visibility_member_class(svc_session: AsyncSession) -> None:
    """EntityVisibilityMember: composite PK over (entity_type, entity_id, stakeholder_id)."""
    sh = Stakeholder(
        id=str(ULID()),
        canonical_name="Member",
        primary_email=f"member-{ULID()}@example.com",
    )
    svc_session.add(sh)
    ev = Event(id=str(ULID()), domain="work", type="process", occurred_at=datetime.now(UTC))
    svc_session.add(ev)
    await svc_session.flush()

    evm = EntityVisibilityMember(
        entity_type="event",
        entity_id=ev.id,
        stakeholder_id=sh.id,
    )
    svc_session.add(evm)
    await svc_session.flush()

    rows = (await svc_session.execute(select(EntityVisibilityMember))).scalars().all()
    assert len(rows) == 1
    assert rows[0].entity_type == "event"

    # CHECK on entity_type
    with pytest.raises(IntegrityError):
        bad = EntityVisibilityMember(
            entity_type="invalid_type",
            entity_id=ev.id,
            stakeholder_id=sh.id,
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()


async def test_stakeholder_role_class(svc_session: AsyncSession) -> None:
    """StakeholderRole: time-bounded universal-10 role periods."""
    sh = Stakeholder(
        id=str(ULID()),
        canonical_name="Role Holder",
        primary_email=f"roleholder-{ULID()}@example.com",
    )
    svc_session.add(sh)
    await svc_session.flush()

    sr = StakeholderRole(
        id=str(ULID()),
        stakeholder_id=sh.id,
        domain="work",
        scope_type="engagement",
        scope_id=str(ULID()),
        role="sponsor",
        started_at=date(2026, 1, 1),
        ended_at=None,
    )
    svc_session.add(sr)
    await svc_session.flush()
    await svc_session.refresh(sr)

    assert sr.role == "sponsor"
    assert sr.started_at == date(2026, 1, 1)
    assert sr.ended_at is None
    assert sr.created_at is not None

    # Invalid role
    with pytest.raises(IntegrityError):
        bad = StakeholderRole(
            id=str(ULID()),
            stakeholder_id=sh.id,
            domain="work",
            scope_type="engagement",
            scope_id=str(ULID()),
            role="champion",  # 9-role enum value, not in universal 10
            started_at=date(2026, 1, 1),
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()


async def test_atom_contribution_class(svc_session: AsyncSession) -> None:
    """AtomContribution: forward-provenance composite PK + CHECK on consumer_type."""
    ev = Event(id=str(ULID()), domain="work", type="process", occurred_at=datetime.now(UTC))
    svc_session.add(ev)
    await svc_session.flush()

    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="decision",
        event_id=ev.id,
        content="x",
        anchor_id="d-001",
    )
    svc_session.add(atom)
    await svc_session.flush()

    ac = AtomContribution(
        atom_id=atom.id,
        consumer_type="brief_run",
        consumer_id=str(ULID()),
    )
    svc_session.add(ac)
    await svc_session.flush()
    await svc_session.refresh(ac)
    assert ac.contributed_at is not None

    # CHECK on consumer_type
    with pytest.raises(IntegrityError):
        bad = AtomContribution(
            atom_id=atom.id,
            consumer_type="invalid_consumer",
            consumer_id=str(ULID()),
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()
