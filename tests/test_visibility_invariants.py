"""Visibility regression test suite — #079.

Codifies the visibility invariants from blueprint §6.4 as integration tests.
All tests are marked with the ``visibility`` marker so they can be run
independently as a required gate:

    uv run pytest -m visibility

No test here should ever be RED unless a privacy-level regression has
occurred in production code. RED = bug, not a failing TDD step.

Deferred (will land with their dependencies):
- test_audience_filtered_summary_uses_filtered_atoms → #080
- test_retracted_entity_excluded → #084
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.api._deps import get_audience, get_session
from loom_core.main import app
from loom_core.services.events import list_events
from loom_core.services.hypotheses import list_state_history, list_state_proposals
from loom_core.storage.models import (
    Arena,
    Domain,
    Engagement,
    EntityVisibilityMember,
    Event,
    Hypothesis,
    HypothesisStateChange,
    Stakeholder,
    TriageItem,
)
from loom_core.storage.session import create_engine, create_session_factory
from loom_core.storage.visibility import Audience, derived_visibility

# ---------------------------------------------------------------------------
# Module-level marker: all tests here are visibility regression tests.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.visibility


# ---------------------------------------------------------------------------
# Shared fixture: service-level DB (same pattern as test_events_service.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def _vis_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    return db_path


@pytest_asyncio.fixture
async def vis_session(_vis_test_db: Path) -> AsyncIterator[AsyncSession]:
    """Bare async session for visibility regression tests."""
    engine = create_engine(_vis_test_db)
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
# B1 — Private event must never leak to a non-self audience
# ---------------------------------------------------------------------------


async def test_private_event_does_not_leak_to_engagement_audience(
    vis_session: AsyncSession,
) -> None:
    """A private event is never returned to a non-self audience.

    Blueprint §6.4: 'private' scope — visible only to self (system owner).
    """
    domain = Domain(id=str(ULID()), display_name="Work", privacy_tier="standard")
    vis_session.add(domain)
    await vis_session.flush()

    event = Event(
        id=str(ULID()),
        domain=domain.id,
        type="process",
        occurred_at=datetime.now(UTC),
        visibility_scope="private",
    )
    vis_session.add(event)
    await vis_session.commit()

    async with app.state.session_factory() as session2:
        # Non-self audience must NOT see the private event.
        stakeholder_audience = Audience.for_stakeholders(["s1"])
        rows = await list_events(session2, domain=domain.id, audience=stakeholder_audience)
        ids = {r.id for r in rows}
        assert (
            event.id not in ids
        ), f"PRIVACY LEAK: private event {event.id!r} visible to stakeholder audience"

        # Self (system owner) MUST see the private event — positive control.
        rows_self = await list_events(session2, domain=domain.id, audience=Audience.for_self())
        ids_self = {r.id for r in rows_self}
        assert (
            event.id in ids_self
        ), "Positive control failed: self audience cannot see private event"


# ---------------------------------------------------------------------------
# B2 — stakeholder_set visibility: audience must be a full subset of members
# ---------------------------------------------------------------------------

# Each tuple: (member_ids, audience_ids, should_match)
_SUBSET_CASES: list[tuple[list[str], list[str], bool]] = [
    # audience ⊆ members → match
    (["s1", "s2"], ["s1"], True),
    # audience == members → match
    (["s1", "s2"], ["s1", "s2"], True),
    # s3 not in members → no match
    (["s1", "s2"], ["s1", "s3"], False),
    # s2 not in members → no match
    (["s1"], ["s1", "s2"], False),
    # audience strict subset of larger member set → match
    (["s1", "s2", "s3"], ["s1", "s2"], True),
]


@pytest.mark.parametrize(
    "member_ids,audience_ids,should_match",
    _SUBSET_CASES,
    ids=[
        "audience_strict_subset",
        "audience_equals_members",
        "audience_has_nonmember",
        "superset_audience_rejected",
        "audience_subset_of_larger",
    ],
)
async def test_stakeholder_set_requires_full_subset(
    vis_session: AsyncSession,
    member_ids: list[str],
    audience_ids: list[str],
    should_match: bool,
) -> None:
    """stakeholder_set entity is visible only when audience ⊆ entity_members.

    Blueprint §6.4: the safe subset direction is audience ⊆ entity_members.
    The reverse direction (entity_members ⊆ audience) leaks: if members={s1}
    and audience={s1, s2}, s2 should NOT grant access but would under the
    reversed predicate.
    """
    domain = Domain(id=str(ULID()), display_name="Work", privacy_tier="standard")
    vis_session.add(domain)
    await vis_session.flush()

    # Insert Stakeholder rows for every id referenced.
    all_ids = list(dict.fromkeys(member_ids + audience_ids))  # deduplicated, order preserved
    for sid in all_ids:
        vis_session.add(Stakeholder(id=sid, canonical_name=f"Stakeholder {sid}"))
    await vis_session.flush()

    # Insert event with stakeholder_set visibility.
    event = Event(
        id=str(ULID()),
        domain=domain.id,
        type="process",
        occurred_at=datetime.now(UTC),
        visibility_scope="stakeholder_set",
    )
    vis_session.add(event)
    await vis_session.flush()

    # Link the member_ids to the event.
    for sid in member_ids:
        vis_session.add(
            EntityVisibilityMember(
                entity_type="event",
                entity_id=event.id,
                stakeholder_id=sid,
            )
        )
    await vis_session.commit()

    async with app.state.session_factory() as session2:
        audience = Audience.for_stakeholders(audience_ids)
        rows = await list_events(session2, domain=domain.id, audience=audience)
        ids = {r.id for r in rows}
        if should_match:
            assert (
                event.id in ids
            ), f"MISS: event should be visible for members={member_ids}, audience={audience_ids}"
        else:
            assert (
                event.id not in ids
            ), f"LEAK: event must NOT be visible for members={member_ids}, audience={audience_ids}"


# ---------------------------------------------------------------------------
# B3 — derived_visibility: derived atoms inherit the most-restrictive scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scopes,expected",
    [
        (["private", "engagement_scoped"], "private"),
        (["domain_wide", "stakeholder_set"], "stakeholder_set"),
        (["engagement_scoped", "private", "domain_wide"], "private"),
    ],
    ids=["private_beats_engagement", "stakeholder_beats_domain", "private_beats_all"],
)
def test_derived_atom_inherits_intersection(scopes: list[str], expected: str) -> None:
    """derived_visibility returns the most-restrictive (intersection) scope.

    Blueprint §6.4: derived facts inherit the most restrictive of their
    source visibilities. This is a pure unit test — no DB required.
    """
    assert derived_visibility(scopes) == expected


# ---------------------------------------------------------------------------
# B4 — list_state_history: private parent → None (parent-check defence)
# Cascade from #078: verifies the parent-visibility gate in the service.
# ---------------------------------------------------------------------------


async def _insert_hyp_with_history(session: AsyncSession) -> tuple[str, str]:
    """Insert Domain, Arena, Engagement, private Hypothesis + one StateChange row."""
    domain = Domain(id=str(ULID()), display_name="Work", privacy_tier="standard")
    session.add(domain)
    await session.flush()

    arena = Arena(id=str(ULID()), domain=domain.id, name="Corp")
    session.add(arena)
    await session.flush()

    engagement = Engagement(id=str(ULID()), domain=domain.id, arena_id=arena.id, name="Wave")
    session.add(engagement)
    await session.flush()

    hyp = Hypothesis(
        id=str(ULID()),
        domain=domain.id,
        arena_id=arena.id,
        engagement_id=engagement.id,
        layer="engagement",
        title="Test",
        current_progress="proposed",
        current_confidence="medium",
        current_momentum="steady",
        confidence_inferred=True,
        momentum_inferred=True,
        visibility_scope="private",
    )
    session.add(hyp)
    await session.flush()

    sc = HypothesisStateChange(
        id=str(ULID()),
        hypothesis_id=hyp.id,
        dimension="progress",
        old_value="proposed",
        new_value="in_delivery",
        changed_at=datetime.now(UTC),
        changed_by="human_confirmed",
    )
    session.add(sc)
    await session.commit()
    return hyp.id, sc.id


async def test_list_state_history_excluded_for_private_parent(
    vis_session: AsyncSession,
) -> None:
    """list_state_history returns None when the parent hypothesis is private.

    Parent-check defence in depth: even if the caller knows the hypothesis id,
    they cannot read state history if they cannot see the parent hypothesis.
    None is the 404 contract — the route maps None → 404.
    """
    hyp_id, _ = await _insert_hyp_with_history(vis_session)

    async with app.state.session_factory() as session2:
        sh_audience = Audience.for_stakeholders(["s1"])
        result = await list_state_history(session2, hyp_id, audience=sh_audience)
        assert (
            result is None
        ), "LEAK: list_state_history returned rows for private parent with stakeholder audience"

        result_self = await list_state_history(session2, hyp_id, audience=Audience.for_self())
        assert result_self is not None
        assert len(result_self) >= 1, "Positive control failed: self should see state history"


# ---------------------------------------------------------------------------
# B5 — list_state_proposals: private parent → None (parent-check defence)
# Cascade from #078: verifies the parent-visibility gate in the service.
# ---------------------------------------------------------------------------


async def _insert_hyp_with_proposal(session: AsyncSession) -> tuple[str, str]:
    """Insert Domain, Arena, Engagement, private Hypothesis + one pending TriageItem."""
    domain = Domain(id=str(ULID()), display_name="Work", privacy_tier="standard")
    session.add(domain)
    await session.flush()

    arena = Arena(id=str(ULID()), domain=domain.id, name="Corp")
    session.add(arena)
    await session.flush()

    engagement = Engagement(id=str(ULID()), domain=domain.id, arena_id=arena.id, name="Wave")
    session.add(engagement)
    await session.flush()

    hyp = Hypothesis(
        id=str(ULID()),
        domain=domain.id,
        arena_id=arena.id,
        engagement_id=engagement.id,
        layer="engagement",
        title="Test",
        current_progress="proposed",
        current_confidence="medium",
        current_momentum="steady",
        confidence_inferred=True,
        momentum_inferred=True,
        visibility_scope="private",
    )
    session.add(hyp)
    await session.flush()

    proposal = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp.id,
        surfaced_at=datetime.now(UTC),
        resolved_at=None,
    )
    session.add(proposal)
    await session.commit()
    return hyp.id, proposal.id


async def test_list_state_proposals_excluded_for_private_parent(
    vis_session: AsyncSession,
) -> None:
    """list_state_proposals returns None when the parent hypothesis is private.

    Same parent-check defence as B4, covering proposals instead of history.
    """
    hyp_id, _ = await _insert_hyp_with_proposal(vis_session)

    async with app.state.session_factory() as session2:
        sh_audience = Audience.for_stakeholders(["s1"])
        result = await list_state_proposals(session2, hyp_id, audience=sh_audience)
        assert (
            result is None
        ), "LEAK: list_state_proposals returned rows for private parent with stakeholder audience"

        result_self = await list_state_proposals(session2, hyp_id, audience=Audience.for_self())
        assert result_self is not None
        assert len(result_self) >= 1, "Positive control failed: self should see pending proposals"


# ---------------------------------------------------------------------------
# B6 — HTTP layer: Depends(get_audience) wiring verified end-to-end
# Override the dependency at the app level to inject a stakeholder audience,
# then assert that private events are filtered at the route layer.
# ---------------------------------------------------------------------------


async def test_route_get_events_filters_for_stakeholder_audience(
    client: AsyncClient,
) -> None:
    """GET /v1/events filters private events when audience is a stakeholder.

    Verifies the full dependency wiring: get_audience → list_events predicate.
    The client fixture uses app.state.session_factory, so we can use the same
    factory to insert a domain_wide event for the positive control.
    """
    domain_resp = await client.post(
        "/v1/events",
        json={
            "domain": "work",
            "type": "process",
            "occurred_at": "2026-04-26T10:00:00+00:00",
        },
    )
    assert domain_resp.status_code == 201
    private_id = domain_resp.json()["id"]
    # Default visibility_scope for Event is 'private' (server default).

    # Insert a domain_wide event directly via the session factory
    # (POST /v1/events doesn't accept visibility_scope — deferred to Phase B).
    async with app.state.session_factory() as db:
        public_event = Event(
            id=str(ULID()),
            domain="work",
            type="process",
            occurred_at=datetime(2026, 4, 27, 10, 0, tzinfo=UTC),
            visibility_scope="domain_wide",
        )
        db.add(public_event)
        await db.commit()
        public_id = public_event.id

    # Override get_audience to return a stakeholder (non-self) audience.
    app.dependency_overrides[get_audience] = lambda: Audience.for_stakeholders(["s1"])
    try:
        resp = await client.get("/v1/events?domain=work")
        assert resp.status_code == 200
        events = resp.json()["events"]
        ids = {e["id"] for e in events}

        # Private event must NOT appear.
        assert (
            private_id not in ids
        ), f"LEAK: private event {private_id!r} visible via route to stakeholder audience"
        # Domain-wide event MUST appear.
        assert (
            public_id in ids
        ), f"MISS: domain_wide event {public_id!r} not visible to stakeholder audience"
    finally:
        app.dependency_overrides.pop(get_audience, None)
