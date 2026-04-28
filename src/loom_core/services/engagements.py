"""Engagement service — CRUD and business logic for engagements."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Arena, Engagement, Hypothesis, WorkEngagementMetadata

log = structlog.get_logger()


class ArenaNotFoundError(Exception):
    """Raised when the referenced arena does not exist."""


class EngagementAlreadyClosedError(Exception):
    """Raised when attempting to close an engagement that is already ended."""


async def get_engagement(
    session: AsyncSession,
    engagement_id: str,
) -> tuple[Engagement, WorkEngagementMetadata | None] | None:
    """Return (engagement, metadata_or_None), or None if not found."""
    engagement = await session.get(Engagement, engagement_id)
    if engagement is None:
        return None
    result = await session.execute(
        select(WorkEngagementMetadata).where(WorkEngagementMetadata.engagement_id == engagement_id)
    )
    metadata = result.scalar_one_or_none()
    return engagement, metadata


async def create_engagement(
    session: AsyncSession,
    *,
    domain: str,
    arena_id: str,
    name: str,
    type_tag: str | None = None,
    started_at: datetime | None = None,
) -> Engagement:
    """Create and persist a new engagement row.

    Args:
        session: Active async database session.
        domain: Domain identifier (e.g. ``"work"``).
        arena_id: ULID of the parent arena.
        name: Human-readable engagement name.
        type_tag: Optional type label (e.g. ``"delivery_wave"``).
        started_at: Optional start timestamp.

    Returns:
        The newly created :class:`Engagement` instance.

    Raises:
        ArenaNotFoundError: If no arena row exists with the given ``arena_id``.
    """
    arena = await session.get(Arena, arena_id)
    if arena is None:
        raise ArenaNotFoundError(arena_id)

    engagement = Engagement(
        id=str(ULID()),
        domain=domain,
        arena_id=arena_id,
        name=name,
        type_tag=type_tag,
        started_at=started_at,
    )
    session.add(engagement)
    await session.flush()
    await session.refresh(engagement)
    return engagement


async def update_engagement(
    session: AsyncSession,
    engagement_id: str,
    *,
    name: str | None = None,
    type_tag: str | None = None,
    started_at: datetime | None = None,
    work_metadata: object | None = None,
) -> tuple[Engagement, WorkEngagementMetadata | None] | None:
    """Partially update an engagement's mutable fields.

    Only fields that are not None are updated (sentinel-aware). ended_at is
    intentionally excluded — closing is done via close_engagement() only.
    If ``work_metadata`` is provided, its non-None fields are upserted into
    ``work_engagement_metadata``.

    Returns:
        (engagement, metadata_or_None) after update, or None if not found.
    """
    engagement = await session.get(Engagement, engagement_id)
    if engagement is None:
        return None

    if name is not None:
        engagement.name = name
    if type_tag is not None:
        engagement.type_tag = type_tag
    if started_at is not None:
        engagement.started_at = started_at

    meta: WorkEngagementMetadata | None = None
    if work_metadata is not None:
        result = await session.execute(
            select(WorkEngagementMetadata).where(
                WorkEngagementMetadata.engagement_id == engagement_id
            )
        )
        meta = result.scalar_one_or_none()
        if meta is None:
            meta = WorkEngagementMetadata(engagement_id=engagement_id)
            session.add(meta)

        for field, value in work_metadata.__dict__.items():
            if not field.startswith("_") and value is not None:
                setattr(meta, field, value)
    else:
        result = await session.execute(
            select(WorkEngagementMetadata).where(
                WorkEngagementMetadata.engagement_id == engagement_id
            )
        )
        meta = result.scalar_one_or_none()

    await session.flush()
    await session.refresh(engagement)
    if meta is not None:
        await session.refresh(meta)
    return engagement, meta


async def close_engagement(
    session: AsyncSession,
    engagement_id: str,
    *,
    force: bool = False,
    override_reason: str | None = None,
) -> tuple[Engagement, WorkEngagementMetadata | None, int] | None:
    """Set ended_at on an engagement to now().

    Args:
        session: Active async database session.
        engagement_id: ULID of the engagement to close.
        force: If True, close even if open hypotheses exist.
        override_reason: Required when force=True; logged at INFO level.

    Returns:
        (engagement, metadata_or_None, open_hypothesis_count), or None if not found.

    Raises:
        EngagementAlreadyClosedError: If ended_at is already set.
    """
    engagement = await session.get(Engagement, engagement_id)
    if engagement is None:
        return None
    if engagement.ended_at is not None:
        raise EngagementAlreadyClosedError(engagement_id)

    count_result = await session.execute(
        select(func.count())
        .select_from(Hypothesis)
        .where(
            Hypothesis.engagement_id == engagement_id,
            Hypothesis.closed_at.is_(None),
        )
    )
    open_count: int = count_result.scalar_one()

    if force and override_reason:
        log.info(
            "force-closing engagement",
            engagement_id=engagement_id,
            open_hypotheses=open_count,
            override_reason=override_reason,
        )
        # TODO(003-followup): persist override_reason to an audit row so it
        # is queryable. No schema column exists today; deferred to a later issue.

    engagement.ended_at = datetime.now(UTC)

    result = await session.execute(
        select(WorkEngagementMetadata).where(WorkEngagementMetadata.engagement_id == engagement_id)
    )
    meta = result.scalar_one_or_none()

    await session.flush()
    await session.refresh(engagement)
    return engagement, meta, open_count


async def list_engagements(
    session: AsyncSession,
    *,
    domain: str,
    arena_id: str | None = None,
    closed: bool | None = None,
) -> Sequence[Engagement]:
    """Return engagements matching the given filters.

    Args:
        session: Active async database session.
        domain: Domain to scope the query.
        arena_id: Optional arena filter.
        closed: If ``False``, exclude rows where ``ended_at IS NOT NULL``.
                If ``True``, include only closed engagements.
                If ``None``, return all.

    Returns:
        List of :class:`Engagement` rows ordered by ``created_at DESC``.
    """
    stmt = select(Engagement).where(Engagement.domain == domain)

    if arena_id is not None:
        stmt = stmt.where(Engagement.arena_id == arena_id)

    if closed is False:
        stmt = stmt.where(Engagement.ended_at.is_(None))
    elif closed is True:
        stmt = stmt.where(Engagement.ended_at.is_not(None))

    stmt = stmt.order_by(Engagement.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
