"""Service-level tests for hypothesis close state machine.

Testing at the service level is cleaner here because the close state machine
has 5 input progress states. HTTP-layer testing would require inserting 5
hypotheses with different current_progress values — more noise, same signal.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.services.hypotheses import (
    HypothesisAlreadyClosedError,
    HypothesisNotTerminalError,
    InvalidOverrideReasonError,
    StateChangeProposalAlreadyResolvedError,
    StateChangeProposalNotFoundError,
    close_hypothesis,
    confirm_state_proposal,
    get_hypothesis,
    list_hypotheses,
    list_state_history,
    list_state_proposals,
    override_state_proposal,
)
from loom_core.storage.models import (
    Arena,
    Engagement,
    Hypothesis,
    HypothesisStateChange,
    TriageItem,
)
from loom_core.storage.session import create_engine, create_session_factory
from loom_core.storage.visibility import Audience


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

    # Keep the same override pattern so conftest.client doesn't conflict.
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


async def _insert_hypothesis(
    session: AsyncSession, *, current_progress: str, closed_at: datetime | None = None
) -> str:
    """Insert a minimal valid hypothesis and return its ID."""
    arena = Arena(id=str(ULID()), domain="work", name="Test Corp")
    session.add(arena)
    await session.flush()

    engagement = Engagement(
        id=str(ULID()),
        domain="work",
        arena_id=arena.id,
        name="Wave",
    )
    session.add(engagement)
    await session.flush()

    hyp = Hypothesis(
        id=str(ULID()),
        domain="work",
        arena_id=arena.id,
        engagement_id=engagement.id,
        layer="engagement",
        title="Test",
        current_progress=current_progress,
        current_confidence="medium",
        current_momentum="steady",
        confidence_inferred=True,
        momentum_inferred=True,
        closed_at=closed_at,
    )
    session.add(hyp)
    await session.flush()
    return hyp.id


@pytest.mark.parametrize(
    "progress, should_close",
    [
        ("realised", True),
        ("confirmed", True),
        ("dead", True),
        ("proposed", False),
        ("in_delivery", False),
    ],
)
async def test_close_hypothesis_terminal_state_required(
    svc_session: AsyncSession, progress: str, should_close: bool
) -> None:
    """Terminal states close; non-terminal states raise HypothesisNotTerminalError."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress=progress)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        if should_close:
            result = await close_hypothesis(session2, hyp_id)
            assert result is not None
            assert result.closed_at is not None
            await session2.commit()
        else:
            with pytest.raises(HypothesisNotTerminalError) as exc_info:
                await close_hypothesis(session2, hyp_id)
            assert exc_info.value.args[0] == progress
            await session2.rollback()


async def test_close_hypothesis_already_closed_raises(svc_session: AsyncSession) -> None:
    """Calling close_hypothesis on an already-closed hypothesis raises AlreadyClosedError."""
    already_closed_at = datetime(2026, 1, 1, tzinfo=UTC)
    hyp_id = await _insert_hypothesis(
        svc_session, current_progress="realised", closed_at=already_closed_at
    )
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        with pytest.raises(HypothesisAlreadyClosedError):
            await close_hypothesis(session2, hyp_id)
        await session2.rollback()


# ---------------------------------------------------------------------------
# B5 (#006): cross-hypothesis proposal raises NotFound
# ---------------------------------------------------------------------------


async def test_confirm_state_proposal_for_different_hypothesis_raises(
    svc_session: AsyncSession,
) -> None:
    """confirm raises StateChangeProposalNotFoundError when proposal belongs to a different hypothesis."""
    hyp_a_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    hyp_b_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    proposal = await _insert_pending_proposal(svc_session, hypothesis_id=hyp_b_id)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        with pytest.raises(StateChangeProposalNotFoundError):
            await confirm_state_proposal(
                session2,
                hypothesis_id=hyp_a_id,
                proposal_id=proposal.id,
                dimension="progress",
                new_value="in_delivery",
            )
        await session2.rollback()


# ---------------------------------------------------------------------------
# B7: list_state_history ordering
# ---------------------------------------------------------------------------


async def test_list_state_history_orders_changed_at_desc(svc_session: AsyncSession) -> None:
    """list_state_history returns rows ordered by changed_at DESC."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    t1 = datetime(2026, 4, 1, 8, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 1, 8, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 4, 1, 8, 20, 0, tzinfo=UTC)

    for dimension, changed_at in [
        ("progress", t1),
        ("confidence", t2),
        ("momentum", t3),
    ]:
        row = HypothesisStateChange(
            id=str(ULID()),
            hypothesis_id=hyp_id,
            dimension=dimension,
            old_value=None,
            new_value="proposed" if dimension == "progress" else "medium",
            changed_at=changed_at,
            changed_by="cron_inferred",
        )
        svc_session.add(row)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        rows = await list_state_history(session2, hyp_id, audience=Audience.for_self())

    assert rows is not None
    assert len(rows) == 3
    timestamps = [r.changed_at for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# B8: list_state_history dimension filter
# ---------------------------------------------------------------------------


async def test_list_state_history_filters_by_dimension(svc_session: AsyncSession) -> None:
    """list_state_history filters correctly by dimension."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    for dimension, value in [
        ("progress", "proposed"),
        ("progress", "in_delivery"),
        ("confidence", "high"),
        ("momentum", "slowing"),
    ]:
        row = HypothesisStateChange(
            id=str(ULID()),
            hypothesis_id=hyp_id,
            dimension=dimension,
            old_value=None,
            new_value=value,
            changed_at=datetime.now(UTC),
            changed_by="human_confirmed",
        )
        svc_session.add(row)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        progress_rows = await list_state_history(
            session2, hyp_id, audience=Audience.for_self(), dimension="progress"
        )
        confidence_rows = await list_state_history(
            session2, hyp_id, audience=Audience.for_self(), dimension="confidence"
        )

    assert progress_rows is not None
    assert len(progress_rows) == 2
    assert all(r.dimension == "progress" for r in progress_rows)

    assert confidence_rows is not None
    assert len(confidence_rows) == 1
    assert confidence_rows[0].dimension == "confidence"


# ---------------------------------------------------------------------------
# B9: list_state_proposals filtering
# ---------------------------------------------------------------------------


async def test_list_state_proposals_returns_only_pending_state_change_proposals(
    svc_session: AsyncSession,
) -> None:
    """list_state_proposals returns only unresolved state_change_proposal items for the hypothesis."""
    hyp_a_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    hyp_b_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    now = datetime.now(UTC)

    # (i) pending proposal for A — should appear
    item_i = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_a_id,
        surfaced_at=now,
        resolved_at=None,
    )
    # (ii) resolved proposal for A — should be excluded
    item_ii = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_a_id,
        surfaced_at=now,
        resolved_at=now,
    )
    # (iii) different item_type for A — should be excluded
    item_iii = TriageItem(
        id=str(ULID()),
        item_type="low_confidence_atom",
        related_entity_type="hypothesis",
        related_entity_id=hyp_a_id,
        surfaced_at=now,
        resolved_at=None,
    )
    # (iv) pending proposal but for hypothesis B — should be excluded
    item_iv = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_b_id,
        surfaced_at=now,
        resolved_at=None,
    )

    for item in [item_i, item_ii, item_iii, item_iv]:
        svc_session.add(item)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        proposals = await list_state_proposals(session2, hyp_a_id, audience=Audience.for_self())

    assert proposals is not None
    assert len(proposals) == 1
    assert proposals[0].id == item_i.id


# ---------------------------------------------------------------------------
# B1 (#006): confirm_state_proposal — progress dimension
# ---------------------------------------------------------------------------


async def _insert_pending_proposal(session: AsyncSession, *, hypothesis_id: str) -> TriageItem:
    """Insert a minimal pending state_change_proposal triage item and return it."""
    item = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hypothesis_id,
        resolved_at=None,
    )
    session.add(item)
    await session.flush()
    return item


async def test_confirm_state_proposal_progress_dimension(svc_session: AsyncSession) -> None:
    """confirm_state_proposal updates progress dimension and writes audit row."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    proposal = await _insert_pending_proposal(svc_session, hypothesis_id=hyp_id)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        result = await confirm_state_proposal(
            session2,
            hypothesis_id=hyp_id,
            proposal_id=proposal.id,
            dimension="progress",
            new_value="in_delivery",
        )
        await session2.commit()

    assert result.dimension == "progress"
    assert result.old_value == "proposed"
    assert result.new_value == "in_delivery"
    assert result.changed_by == "human_confirmed"
    assert result.override_reason is None

    async with app.state.session_factory() as session3:
        hyp = await session3.get(Hypothesis, hyp_id)
        assert hyp is not None
        assert hyp.current_progress == "in_delivery"
        assert hyp.progress_last_changed_at is not None

        tri = await session3.get(TriageItem, proposal.id)
        assert tri is not None
        assert tri.resolved_at is not None
        assert tri.resolution == "confirmed"


# ---------------------------------------------------------------------------
# B2 (#006): confirm_state_proposal — confidence dimension
# ---------------------------------------------------------------------------


async def test_confirm_state_proposal_confidence_dimension(svc_session: AsyncSession) -> None:
    """confirm_state_proposal updates confidence and sets confidence_inferred=False."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    proposal = await _insert_pending_proposal(svc_session, hypothesis_id=hyp_id)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        result = await confirm_state_proposal(
            session2,
            hypothesis_id=hyp_id,
            proposal_id=proposal.id,
            dimension="confidence",
            new_value="high",
        )
        await session2.commit()

    assert result.dimension == "confidence"
    assert result.old_value == "medium"
    assert result.new_value == "high"
    assert result.changed_by == "human_confirmed"

    async with app.state.session_factory() as session3:
        hyp = await session3.get(Hypothesis, hyp_id)
        assert hyp is not None
        assert hyp.current_confidence == "high"
        assert hyp.confidence_last_reviewed_at is not None
        assert hyp.confidence_inferred is False


# ---------------------------------------------------------------------------
# B3 (#006): confirm_state_proposal — momentum dimension
# ---------------------------------------------------------------------------


async def test_confirm_state_proposal_momentum_dimension(svc_session: AsyncSession) -> None:
    """confirm_state_proposal updates momentum and sets momentum_inferred=False."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    proposal = await _insert_pending_proposal(svc_session, hypothesis_id=hyp_id)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        result = await confirm_state_proposal(
            session2,
            hypothesis_id=hyp_id,
            proposal_id=proposal.id,
            dimension="momentum",
            new_value="slowing",
        )
        await session2.commit()

    assert result.dimension == "momentum"
    assert result.old_value == "steady"
    assert result.new_value == "slowing"
    assert result.changed_by == "human_confirmed"

    async with app.state.session_factory() as session3:
        hyp = await session3.get(Hypothesis, hyp_id)
        assert hyp is not None
        assert hyp.current_momentum == "slowing"
        assert hyp.momentum_last_reviewed_at is not None
        assert hyp.momentum_inferred is False


# ---------------------------------------------------------------------------
# B4 (#006): already-resolved guard
# ---------------------------------------------------------------------------


async def test_confirm_state_proposal_already_resolved_raises(svc_session: AsyncSession) -> None:
    """confirm raises StateChangeProposalAlreadyResolvedError for an already-resolved proposal."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    now = datetime.now(UTC)
    proposal = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_id,
        resolved_at=now,
        resolution="confirmed",
    )
    svc_session.add(proposal)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        with pytest.raises(StateChangeProposalAlreadyResolvedError):
            await confirm_state_proposal(
                session2,
                hypothesis_id=hyp_id,
                proposal_id=proposal.id,
                dimension="progress",
                new_value="in_delivery",
            )
        await session2.rollback()


# ---------------------------------------------------------------------------
# B7 (#006): override_state_proposal — stores reason and sets inferred=False
# ---------------------------------------------------------------------------


async def test_override_state_proposal_with_reason_stores_verbatim(
    svc_session: AsyncSession,
) -> None:
    """override_state_proposal stores override_reason verbatim and sets changed_by=human_overridden."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    proposal = await _insert_pending_proposal(svc_session, hypothesis_id=hyp_id)
    await svc_session.commit()

    reason = "Steerco chair confirmed sponsorship in 1:1."

    async with app.state.session_factory() as session2:
        result = await override_state_proposal(
            session2,
            hypothesis_id=hyp_id,
            proposal_id=proposal.id,
            dimension="confidence",
            new_value="high",
            override_reason=reason,
        )
        await session2.commit()

    assert result.changed_by == "human_overridden"
    assert result.override_reason == reason
    assert result.old_value == "medium"
    assert result.new_value == "high"
    assert result.dimension == "confidence"

    async with app.state.session_factory() as session3:
        hyp = await session3.get(Hypothesis, hyp_id)
        assert hyp is not None
        assert hyp.current_confidence == "high"
        assert hyp.confidence_inferred is False

        tri = await session3.get(TriageItem, proposal.id)
        assert tri is not None
        assert tri.resolved_at is not None
        assert tri.resolution == "overridden"


# ---------------------------------------------------------------------------
# B8 (#006): whitespace-only override_reason raises InvalidOverrideReasonError
# ---------------------------------------------------------------------------


async def test_override_state_proposal_whitespace_reason_raises(svc_session: AsyncSession) -> None:
    """override raises InvalidOverrideReasonError when override_reason is whitespace-only."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    proposal = await _insert_pending_proposal(svc_session, hypothesis_id=hyp_id)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        with pytest.raises(InvalidOverrideReasonError):
            await override_state_proposal(
                session2,
                hypothesis_id=hyp_id,
                proposal_id=proposal.id,
                dimension="confidence",
                new_value="high",
                override_reason="   ",
            )
        await session2.rollback()


async def test_get_hypothesis_honours_audience(svc_session: AsyncSession) -> None:
    """get_hypothesis filters out hypotheses not visible to the audience."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    # Make it private
    hyp = await svc_session.get(Hypothesis, hyp_id)
    assert hyp is not None
    hyp.visibility_scope = "private"
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        # Self audience sees private
        h1 = await get_hypothesis(session2, hyp_id, audience=Audience.for_self())
        assert h1 is not None

        # Stakeholder audience does not see private
        sh_audience = Audience.for_stakeholders(["SH_1"])
        h2 = await get_hypothesis(session2, hyp_id, audience=sh_audience)
        assert h2 is None


async def test_list_hypotheses_honours_audience(svc_session: AsyncSession) -> None:
    """list_hypotheses filters out hypotheses not visible to the audience."""
    hyp1_id = await _insert_hypothesis(svc_session, current_progress="proposed")
    hyp2_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    hyp1 = await svc_session.get(Hypothesis, hyp1_id)
    assert hyp1 is not None
    hyp1.visibility_scope = "domain_wide"

    # Make hyp2 private
    hyp2 = await svc_session.get(Hypothesis, hyp2_id)
    assert hyp2 is not None
    hyp2.visibility_scope = "private"
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        # Self audience sees both
        rows = await list_hypotheses(session2, audience=Audience.for_self())
        assert len(rows) == 2

        # Stakeholder audience sees only public
        sh_audience = Audience.for_stakeholders(["SH_1"])
        rows_sh = await list_hypotheses(session2, audience=sh_audience)
        assert len(rows_sh) == 1
        assert rows_sh[0].id == hyp1_id


async def test_list_state_history_honours_audience(svc_session: AsyncSession) -> None:
    """list_state_history returns None if the parent hypothesis is not visible."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    # Insert a state-change row directly so history exists.
    state_change = HypothesisStateChange(
        id=str(ULID()),
        hypothesis_id=hyp_id,
        dimension="progress",
        old_value="proposed",
        new_value="in_delivery",
        changed_at=datetime.now(UTC),
        changed_by="human_confirmed",
    )
    svc_session.add(state_change)
    await svc_session.commit()

    # Make hypothesis private
    hyp = await svc_session.get(Hypothesis, hyp_id)
    assert hyp is not None
    hyp.visibility_scope = "private"
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        # Self audience sees history
        history_self = await list_state_history(session2, hyp_id, audience=Audience.for_self())
        assert history_self is not None
        assert len(history_self) > 0

        # Stakeholder audience sees None (behaves as NOT_FOUND)
        sh_audience = Audience.for_stakeholders(["SH_1"])
        history_sh = await list_state_history(session2, hyp_id, audience=sh_audience)
        assert history_sh is None


async def test_list_state_proposals_honours_audience(svc_session: AsyncSession) -> None:
    """list_state_proposals returns None if the parent hypothesis is not visible."""
    hyp_id = await _insert_hypothesis(svc_session, current_progress="proposed")

    # Add a pending state-change proposal.
    proposal = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_id,
        surfaced_at=datetime.now(UTC),
        resolved_at=None,
    )
    svc_session.add(proposal)

    # Make hypothesis private
    hyp = await svc_session.get(Hypothesis, hyp_id)
    assert hyp is not None
    hyp.visibility_scope = "private"
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        # Self audience sees proposals
        proposals_self = await list_state_proposals(session2, hyp_id, audience=Audience.for_self())
        assert proposals_self is not None
        assert len(proposals_self) > 0

        # Stakeholder audience sees None (behaves as NOT_FOUND)
        sh_audience = Audience.for_stakeholders(["SH_1"])
        proposals_sh = await list_state_proposals(session2, hyp_id, audience=sh_audience)
        assert proposals_sh is None
