"""TDD: Visibility filter library and Audience type (#077).

Behaviours covered:
  B1: Audience is a frozen dataclass
  B2: Audience.for_self() factory
  B3: Audience.for_stakeholders(ids) factory + empty rejection
  B4: derived_visibility returns most restrictive scope
  B5: visibility_predicate with for_self() matches all four scopes
  B6: non-self audience: domain_wide matches; private and engagement_scoped never match
  B7: stakeholder_set matches iff audience ⊆ entity_members (count-match)
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import EntityVisibilityMember, Event, Stakeholder
from loom_core.storage.session import create_engine, create_session_factory
from loom_core.storage.visibility import Audience, derived_visibility, visibility_predicate

# ---------------------------------------------------------------------------
# Local DB fixture (same pattern as test_orm_v08_cross_cutting.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def _vis_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "vis.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    return db_path


@pytest_asyncio.fixture
async def vis_session(_vis_test_db: Path) -> AsyncIterator[AsyncSession]:
    engine = create_engine(_vis_test_db)
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(ULID())


def _event(domain: str, scope: str, eid: str | None = None) -> Event:
    return Event(
        id=eid or _uid(),
        domain=domain,
        type="process",
        occurred_at=datetime.now(UTC),
        visibility_scope=scope,
    )


def _member(
    entity_id: str, stakeholder_id: str, entity_type: str = "event"
) -> EntityVisibilityMember:
    return EntityVisibilityMember(
        entity_type=entity_type,
        entity_id=entity_id,
        stakeholder_id=stakeholder_id,
    )


# ---------------------------------------------------------------------------
# B1: Audience is a frozen dataclass
# ---------------------------------------------------------------------------


def test_audience_dataclass_construction() -> None:
    """B1: Audience constructs correctly and is frozen (hashable, immutable)."""
    a = Audience(stakeholder_ids=frozenset({"s1"}), is_self=False)
    assert a.stakeholder_ids == frozenset({"s1"})
    assert a.is_self is False


def test_audience_dataclass_is_self_defaults_false() -> None:
    """B1: is_self defaults to False."""
    a = Audience(stakeholder_ids=frozenset({"s1"}))
    assert a.is_self is False


def test_audience_dataclass_hashable() -> None:
    """B1: Audience is hashable (proves frozen dataclass)."""
    a = Audience(stakeholder_ids=frozenset({"s1"}), is_self=False)
    assert hash(a) is not None
    # Can be used in a set
    s = {a}
    assert a in s


def test_audience_dataclass_frozen() -> None:
    """B1: Mutation raises FrozenInstanceError."""
    a = Audience(stakeholder_ids=frozenset({"s1"}), is_self=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.is_self = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# B2: Audience.for_self() factory
# ---------------------------------------------------------------------------


def test_audience_for_self() -> None:
    """B2: for_self() returns is_self=True and empty stakeholder_ids."""
    a = Audience.for_self()
    assert a.is_self is True
    assert a.stakeholder_ids == frozenset()


def test_audience_for_self_hashable() -> None:
    """B2: for_self() result is also hashable."""
    a = Audience.for_self()
    assert hash(a) is not None


# ---------------------------------------------------------------------------
# B3: Audience.for_stakeholders() factory + empty rejection
# ---------------------------------------------------------------------------


def test_audience_for_stakeholders_list() -> None:
    """B3: for_stakeholders() accepts list, returns frozenset, is_self=False."""
    a = Audience.for_stakeholders(["s1", "s2"])
    assert a.is_self is False
    assert a.stakeholder_ids == frozenset({"s1", "s2"})


def test_audience_for_stakeholders_tuple() -> None:
    """B3: for_stakeholders() accepts any Sequence (tuple)."""
    a = Audience.for_stakeholders(("s1", "s3"))
    assert a.stakeholder_ids == frozenset({"s1", "s3"})


def test_audience_for_stakeholders_deduplicates() -> None:
    """B3: frozenset deduplicates repeated ids."""
    a = Audience.for_stakeholders(["s1", "s1", "s2"])
    assert a.stakeholder_ids == frozenset({"s1", "s2"})


def test_audience_for_stakeholders_empty_raises() -> None:
    """B3: for_stakeholders([]) raises ValueError with leak-risk message."""
    with pytest.raises(ValueError, match="empty") as exc_info:
        Audience.for_stakeholders([])
    assert "stakeholder_set" in str(exc_info.value)


# ---------------------------------------------------------------------------
# B4: derived_visibility returns most restrictive scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sources,expected",
    [
        (["domain_wide"], "domain_wide"),
        (["domain_wide", "private"], "private"),
        (["stakeholder_set", "engagement_scoped"], "stakeholder_set"),
        (["private", "domain_wide"], "private"),
        (["engagement_scoped", "domain_wide"], "engagement_scoped"),
        (["stakeholder_set", "private", "domain_wide"], "private"),
        (["domain_wide", "domain_wide"], "domain_wide"),
    ],
)
def test_derived_visibility(sources: list[str], expected: str) -> None:
    """B4: derived_visibility returns the most restrictive (intersection) scope."""
    assert derived_visibility(sources) == expected


def test_derived_visibility_empty_raises() -> None:
    """B4: derived_visibility([]) raises ValueError."""
    with pytest.raises(ValueError):
        derived_visibility([])


# ---------------------------------------------------------------------------
# B5: visibility_predicate — for_self() matches all four scopes
# ---------------------------------------------------------------------------


async def test_visibility_predicate_self_matches_all_scopes(vis_session: AsyncSession) -> None:
    """B5: for_self() audience sees domain_wide, engagement_scoped, stakeholder_set, private."""
    # Migrations seed domain 'work'; use it directly.
    domain_id = "work"

    scopes = ["domain_wide", "engagement_scoped", "stakeholder_set", "private"]
    event_ids = {}
    for scope in scopes:
        ev = _event(domain_id, scope)
        vis_session.add(ev)
        event_ids[scope] = ev.id
    await vis_session.flush()

    stmt = select(Event.id).where(
        visibility_predicate(Event.visibility_scope, "event", Event.id, Audience.for_self())
    )
    result = set((await vis_session.execute(stmt)).scalars().all())

    assert set(event_ids.values()).issubset(
        result
    ), f"Expected all 4 scope events visible to self. Missing: {set(event_ids.values()) - result}"


# ---------------------------------------------------------------------------
# B6: non-self: domain_wide matches; private and engagement_scoped never match
# ---------------------------------------------------------------------------


async def test_visibility_predicate_non_self_domain_wide_only(vis_session: AsyncSession) -> None:
    """B6: Non-self audience: only domain_wide returned; private/engagement_scoped excluded."""
    domain_id = "work"  # seeded by migrations
    s1_id = _uid()
    s1 = Stakeholder(id=s1_id, canonical_name="S1 User")
    vis_session.add(s1)
    await vis_session.flush()

    dw_id = _uid()
    es_id = _uid()
    pr_id = _uid()
    vis_session.add(_event(domain_id, "domain_wide", dw_id))
    vis_session.add(_event(domain_id, "engagement_scoped", es_id))
    vis_session.add(_event(domain_id, "private", pr_id))
    await vis_session.flush()

    audience = Audience.for_stakeholders([s1_id])
    stmt = select(Event.id).where(
        visibility_predicate(Event.visibility_scope, "event", Event.id, audience)
    )
    result = set((await vis_session.execute(stmt)).scalars().all())

    assert dw_id in result, "domain_wide must be in result"
    assert es_id not in result, "engagement_scoped must NOT be in result"
    assert pr_id not in result, "private must NOT be in result"


# ---------------------------------------------------------------------------
# B7: stakeholder_set matches iff audience ⊆ entity_members
# ---------------------------------------------------------------------------


async def _setup_b7(session: AsyncSession) -> tuple[str, str, str, str, str, str, str]:
    """Insert stakeholders and events for B7 subset tests.

    Uses the pre-seeded 'work' domain. Returns
    (domain_id, s1_id, s2_id, s3_id, event_a_id, event_b_id, event_c_id).
    """
    domain_id = "work"  # seeded by migrations
    s1_id = _uid()
    s2_id = _uid()
    s3_id = _uid()
    s1 = Stakeholder(id=s1_id, canonical_name="B7 User S1")
    s2 = Stakeholder(id=s2_id, canonical_name="B7 User S2")
    s3 = Stakeholder(id=s3_id, canonical_name="B7 User S3")
    session.add(s1)
    session.add(s2)
    session.add(s3)
    await session.flush()

    # Event A: members {s1, s2} — audience {s1} → MATCH (s1 ∈ {s1,s2})
    ev_a = _event(domain_id, "stakeholder_set")
    # Event B: members {s1} — audience {s1, s2} → NO MATCH (s2 ∉ {s1})
    ev_b = _event(domain_id, "stakeholder_set")
    # Event C: members {s1, s2, s3} — audience {s1, s2} → MATCH
    ev_c = _event(domain_id, "stakeholder_set")
    # Event D: domain_wide — always included as composition sanity check
    ev_dw = _event(domain_id, "domain_wide")

    session.add(ev_a)
    session.add(ev_b)
    session.add(ev_c)
    session.add(ev_dw)
    await session.flush()

    # Event A membership: {s1, s2}
    session.add(_member(ev_a.id, s1_id))
    session.add(_member(ev_a.id, s2_id))
    # Event B membership: {s1}
    session.add(_member(ev_b.id, s1_id))
    # Event C membership: {s1, s2, s3}
    session.add(_member(ev_c.id, s1_id))
    session.add(_member(ev_c.id, s2_id))
    session.add(_member(ev_c.id, s3_id))
    await session.flush()

    return domain_id, s1_id, s2_id, s3_id, ev_a.id, ev_b.id, ev_c.id


async def test_stakeholder_set_audience_subset_of_members_matches(
    vis_session: AsyncSession,
) -> None:
    """B7a: Event A (members {s1,s2}), audience {s1} → MATCH (s1 ⊆ {s1,s2})."""
    _, s1_id, _, _, ev_a_id, _, _ = await _setup_b7(vis_session)

    audience = Audience.for_stakeholders([s1_id])
    stmt = select(Event.id).where(
        visibility_predicate(Event.visibility_scope, "event", Event.id, audience)
    )
    result = set((await vis_session.execute(stmt)).scalars().all())

    # Event A matches (audience {s1} ⊆ members {s1, s2}).
    # domain_wide event also included.
    assert ev_a_id in result, "Event A should match: audience {s1} ⊆ members {s1, s2}"


async def test_stakeholder_set_audience_not_subset_no_match(
    vis_session: AsyncSession,
) -> None:
    """B7b: Event B (members {s1}), audience {s1, s2} → NO MATCH (s2 ∉ members)."""
    _, s1_id, s2_id, _, _, ev_b_id, _ = await _setup_b7(vis_session)

    audience = Audience.for_stakeholders([s1_id, s2_id])
    stmt = select(Event.id).where(
        visibility_predicate(Event.visibility_scope, "event", Event.id, audience)
    )
    result = set((await vis_session.execute(stmt)).scalars().all())

    assert ev_b_id not in result, "Event B must NOT match: s2 ∉ members {s1}"


async def test_stakeholder_set_larger_member_set_matches_smaller_audience(
    vis_session: AsyncSession,
) -> None:
    """B7c: Event C (members {s1,s2,s3}), audience {s1,s2} → MATCH (audience ⊆ members)."""
    _, s1_id, s2_id, _, _, _, ev_c_id = await _setup_b7(vis_session)

    audience = Audience.for_stakeholders([s1_id, s2_id])
    stmt = select(Event.id).where(
        visibility_predicate(Event.visibility_scope, "event", Event.id, audience)
    )
    result = set((await vis_session.execute(stmt)).scalars().all())

    assert ev_c_id in result, "Event C should match: audience {s1,s2} ⊆ members {s1,s2,s3}"
