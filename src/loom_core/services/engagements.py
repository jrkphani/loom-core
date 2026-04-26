"""Engagement service — CRUD and business logic for engagements."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Arena, Engagement


class ArenaNotFoundError(Exception):
    """Raised when the referenced arena does not exist."""


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


async def list_engagements(
    session: AsyncSession,
    *,
    domain: str,
    arena_id: str | None = None,
    closed: bool | None = None,
) -> list[Engagement]:
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
    return list(result.scalars().all())
