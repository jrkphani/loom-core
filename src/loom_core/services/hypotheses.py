"""Hypothesis service — CRUD and business logic for hypotheses."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import (
    Arena,
    Engagement,
    Hypothesis,
    HypothesisStateChange,
    TriageItem,
)


class ArenaNotFoundError(Exception):
    """Raised when the referenced arena does not exist."""


class EngagementNotFoundError(Exception):
    """Raised when the referenced engagement does not exist."""


class HypothesisAlreadyClosedError(Exception):
    """Raised when attempting to close a hypothesis that is already closed."""


class HypothesisNotTerminalError(Exception):
    """Raised when current_progress is not a terminal state at close time.

    args[0] is the actual current_progress value.
    """


class StateChangeProposalNotFoundError(Exception):
    """Raised when proposal_id doesn't match a pending proposal for the hypothesis."""


class StateChangeProposalAlreadyResolvedError(Exception):
    """Raised when the proposal has already been resolved."""


class InvalidOverrideReasonError(Exception):
    """Raised when override_reason is empty or whitespace-only."""


_TERMINAL_STATES = frozenset({"realised", "confirmed", "dead"})


async def create_hypothesis(
    session: AsyncSession,
    *,
    domain: str,
    arena_id: str,
    engagement_id: str | None,
    layer: str,
    title: str,
    description: str | None = None,
) -> Hypothesis:
    """Create and persist a new hypothesis row.

    Args:
        session: Active async database session.
        domain: Domain identifier (e.g. ``"work"``).
        arena_id: ULID of the parent arena.
        engagement_id: ULID of the parent engagement, or None for arena-level.
        layer: ``"engagement"`` or ``"arena"``.
        title: Hypothesis title.
        description: Optional free-text description.

    Returns:
        The newly created :class:`Hypothesis` instance.

    Raises:
        ArenaNotFoundError: If ``arena_id`` does not exist.
        EngagementNotFoundError: If ``layer == "engagement"`` and
            ``engagement_id`` does not exist.
    """
    arena = await session.get(Arena, arena_id)
    if arena is None:
        raise ArenaNotFoundError(arena_id)

    if layer == "engagement" and engagement_id is not None:
        engagement = await session.get(Engagement, engagement_id)
        if engagement is None:
            raise EngagementNotFoundError(engagement_id)

    hypothesis = Hypothesis(
        id=str(ULID()),
        domain=domain,
        arena_id=arena_id,
        engagement_id=engagement_id,
        layer=layer,
        title=title,
        description=description,
    )
    session.add(hypothesis)
    await session.flush()
    await session.refresh(hypothesis)
    return hypothesis


async def get_hypothesis(
    session: AsyncSession,
    hypothesis_id: str,
) -> Hypothesis | None:
    """Return a hypothesis by ID, or None if not found."""
    return await session.get(Hypothesis, hypothesis_id)


async def list_hypotheses(
    session: AsyncSession,
    *,
    engagement_id: str | None = None,
    arena_id: str | None = None,
    layer: str | None = None,
) -> Sequence[Hypothesis]:
    """Return hypotheses matching the given filters, ordered by created_at DESC.

    Args:
        session: Active async database session.
        engagement_id: Filter by engagement.
        arena_id: Filter by arena.
        layer: Filter by layer (``"engagement"`` or ``"arena"``).

    Returns:
        Sequence of matching :class:`Hypothesis` rows.
    """
    stmt = select(Hypothesis)

    if engagement_id is not None:
        stmt = stmt.where(Hypothesis.engagement_id == engagement_id)
    if arena_id is not None:
        stmt = stmt.where(Hypothesis.arena_id == arena_id)
    if layer is not None:
        stmt = stmt.where(Hypothesis.layer == layer)

    stmt = stmt.order_by(Hypothesis.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def update_hypothesis(
    session: AsyncSession,
    hypothesis_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
) -> Hypothesis | None:
    """Partially update a hypothesis's title and/or description.

    Only fields that are not None are updated (sentinel-aware). State fields
    are deliberately excluded — those go through the state-change mechanism.

    Returns:
        Updated :class:`Hypothesis`, or None if not found.
    """
    hypothesis = await session.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        return None

    if title is not None:
        hypothesis.title = title
    if description is not None:
        hypothesis.description = description

    await session.flush()
    await session.refresh(hypothesis)
    return hypothesis


async def list_state_history(
    session: AsyncSession,
    hypothesis_id: str,
    *,
    dimension: str | None = None,
) -> Sequence[HypothesisStateChange] | None:
    """Return state change rows for a hypothesis, ordered by changed_at DESC.

    Returns None if the hypothesis does not exist (route maps to 404). Otherwise
    returns all rows, optionally filtered to a single dimension.
    """
    hypothesis = await session.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        return None

    stmt = select(HypothesisStateChange).where(HypothesisStateChange.hypothesis_id == hypothesis_id)
    if dimension is not None:
        stmt = stmt.where(HypothesisStateChange.dimension == dimension)
    stmt = stmt.order_by(HypothesisStateChange.changed_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def list_state_proposals(
    session: AsyncSession,
    hypothesis_id: str,
) -> Sequence[TriageItem] | None:
    """Return pending state-change proposal triage items for a hypothesis.

    Returns None if the hypothesis does not exist (route maps to 404). Otherwise
    returns triage_items rows where item_type='state_change_proposal',
    related_entity_id=hypothesis_id, and resolved_at IS NULL, ordered by
    surfaced_at DESC.
    """
    hypothesis = await session.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        return None

    stmt = (
        select(TriageItem)
        .where(TriageItem.item_type == "state_change_proposal")
        .where(TriageItem.related_entity_id == hypothesis_id)
        .where(TriageItem.resolved_at.is_(None))
        .order_by(TriageItem.surfaced_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def confirm_state_proposal(
    session: AsyncSession,
    *,
    hypothesis_id: str,
    proposal_id: str,
    dimension: str,
    new_value: str,
) -> HypothesisStateChange:
    """Confirm a pending state-change proposal.

    Raises:
        StateChangeProposalNotFoundError: If proposal_id does not belong to this hypothesis.
        StateChangeProposalAlreadyResolvedError: If the proposal is already resolved.
    """
    result = await session.execute(
        select(TriageItem).where(
            TriageItem.id == proposal_id,
            TriageItem.related_entity_id == hypothesis_id,
            TriageItem.item_type == "state_change_proposal",
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise StateChangeProposalNotFoundError(proposal_id)
    if proposal.resolved_at is not None:
        raise StateChangeProposalAlreadyResolvedError(proposal_id)

    hypothesis = await session.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        raise StateChangeProposalNotFoundError(hypothesis_id)

    now = datetime.now(UTC)
    old_value: str | None

    if dimension == "progress":
        old_value = hypothesis.current_progress
        hypothesis.current_progress = new_value
        hypothesis.progress_last_changed_at = now
    elif dimension == "confidence":
        old_value = hypothesis.current_confidence
        hypothesis.current_confidence = new_value
        hypothesis.confidence_last_reviewed_at = now
        hypothesis.confidence_inferred = False
    elif dimension == "momentum":
        old_value = hypothesis.current_momentum
        hypothesis.current_momentum = new_value
        hypothesis.momentum_last_reviewed_at = now
        hypothesis.momentum_inferred = False
    else:
        old_value = None

    state_change = HypothesisStateChange(
        id=str(ULID()),
        hypothesis_id=hypothesis_id,
        dimension=dimension,
        old_value=old_value,
        new_value=new_value,
        changed_at=now,
        changed_by="human_confirmed",
    )
    session.add(state_change)

    proposal.resolved_at = now
    proposal.resolution = "confirmed"

    await session.flush()
    await session.refresh(state_change)
    return state_change


async def override_state_proposal(
    session: AsyncSession,
    *,
    hypothesis_id: str,
    proposal_id: str,
    dimension: str,
    new_value: str,
    override_reason: str,
) -> HypothesisStateChange:
    """Override a pending state-change proposal with a human-chosen value and mandatory reason.

    Raises:
        StateChangeProposalNotFoundError: If proposal_id does not belong to this hypothesis.
        StateChangeProposalAlreadyResolvedError: If the proposal is already resolved.
        InvalidOverrideReasonError: If override_reason is empty or whitespace-only.
    """
    result = await session.execute(
        select(TriageItem).where(
            TriageItem.id == proposal_id,
            TriageItem.related_entity_id == hypothesis_id,
            TriageItem.item_type == "state_change_proposal",
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise StateChangeProposalNotFoundError(proposal_id)
    if proposal.resolved_at is not None:
        raise StateChangeProposalAlreadyResolvedError(proposal_id)

    if not override_reason.strip():
        raise InvalidOverrideReasonError(override_reason)

    hypothesis = await session.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        raise StateChangeProposalNotFoundError(hypothesis_id)

    now = datetime.now(UTC)
    old_value: str | None

    if dimension == "progress":
        old_value = hypothesis.current_progress
        hypothesis.current_progress = new_value
        hypothesis.progress_last_changed_at = now
    elif dimension == "confidence":
        old_value = hypothesis.current_confidence
        hypothesis.current_confidence = new_value
        hypothesis.confidence_last_reviewed_at = now
        hypothesis.confidence_inferred = False
    elif dimension == "momentum":
        old_value = hypothesis.current_momentum
        hypothesis.current_momentum = new_value
        hypothesis.momentum_last_reviewed_at = now
        hypothesis.momentum_inferred = False
    else:
        old_value = None

    state_change = HypothesisStateChange(
        id=str(ULID()),
        hypothesis_id=hypothesis_id,
        dimension=dimension,
        old_value=old_value,
        new_value=new_value,
        changed_at=now,
        changed_by="human_overridden",
        override_reason=override_reason,
    )
    session.add(state_change)

    proposal.resolved_at = now
    proposal.resolution = "overridden"

    await session.flush()
    await session.refresh(state_change)
    return state_change


async def close_hypothesis(
    session: AsyncSession,
    hypothesis_id: str,
) -> Hypothesis | None:
    """Set closed_at on a hypothesis to now().

    Returns:
        Updated :class:`Hypothesis`, or None if not found.

    Raises:
        HypothesisAlreadyClosedError: If ``closed_at`` is already set.
        HypothesisNotTerminalError: If ``current_progress`` is not a terminal
            state (``realised``, ``confirmed``, or ``dead``).
    """
    hypothesis = await session.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        return None

    if hypothesis.closed_at is not None:
        raise HypothesisAlreadyClosedError(hypothesis_id)

    if hypothesis.current_progress not in _TERMINAL_STATES:
        raise HypothesisNotTerminalError(hypothesis.current_progress)

    hypothesis.closed_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(hypothesis)
    return hypothesis
