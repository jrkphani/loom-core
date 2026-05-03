"""Arena service — CRUD and business logic for arenas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Arena, WorkAccountMetadata
from loom_core.storage.visibility import Audience


class ArenaAlreadyClosedError(Exception):
    """Raised when attempting to close an arena that is already closed."""


async def get_arena(
    session: AsyncSession,
    arena_id: str,
    *,
    audience: Audience,
) -> tuple[Arena, WorkAccountMetadata | None] | None:
    """Return (arena, metadata_or_None), or None if the arena does not exist.

    Args:
        session: Active async database session.
        arena_id: The ID of the arena to fetch.
        audience: Documentary parameter for future visibility filtering.
    """
    arena = await session.get(Arena, arena_id)
    if arena is None:
        return None
    result = await session.execute(
        select(WorkAccountMetadata).where(WorkAccountMetadata.arena_id == arena_id)
    )
    metadata = result.scalar_one_or_none()
    return arena, metadata


async def create_arena(
    session: AsyncSession,
    *,
    domain: str,
    name: str,
    description: str | None = None,
) -> Arena:
    """Create and persist a new arena row.

    Args:
        session: Active async database session.
        domain: Domain identifier (e.g. ``"work"``).
        name: Human-readable arena name.
        description: Optional free-text description.

    Returns:
        The newly created :class:`Arena` instance with all fields populated.
    """
    arena = Arena(
        id=str(ULID()),
        domain=domain,
        name=name,
        description=description,
    )
    session.add(arena)
    await session.flush()  # populates server defaults (created_at) without committing
    await session.refresh(arena)
    return arena


async def update_arena(
    session: AsyncSession,
    arena_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    work_metadata: object | None = None,
) -> tuple[Arena, WorkAccountMetadata | None] | None:
    """Partially update an arena's mutable fields.

    Only the fields that are not None are updated (sentinel-aware).
    If ``work_metadata`` is provided, its non-None fields are upserted into
    ``work_account_metadata``.

    Returns:
        (arena, metadata_or_None) after update, or None if the arena was not
        found.
    """
    arena = await session.get(Arena, arena_id)
    if arena is None:
        return None

    if name is not None:
        arena.name = name
    if description is not None:
        arena.description = description

    meta: WorkAccountMetadata | None = None
    if work_metadata is not None:
        result = await session.execute(
            select(WorkAccountMetadata).where(WorkAccountMetadata.arena_id == arena_id)
        )
        meta = result.scalar_one_or_none()
        if meta is None:
            meta = WorkAccountMetadata(arena_id=arena_id)
            session.add(meta)

        # Pydantic model — iterate the fields that were explicitly set.
        for field, value in work_metadata.__dict__.items():
            if not field.startswith("_") and value is not None:
                setattr(meta, field, value)
    else:
        result = await session.execute(
            select(WorkAccountMetadata).where(WorkAccountMetadata.arena_id == arena_id)
        )
        meta = result.scalar_one_or_none()

    await session.flush()
    await session.refresh(arena)
    if meta is not None:
        await session.refresh(meta)
    return arena, meta


async def close_arena(
    session: AsyncSession,
    arena_id: str,
) -> tuple[Arena, WorkAccountMetadata | None] | None:
    """Set closed_at on an arena to now().

    Returns:
        (arena, metadata_or_None) after close, or None if not found.

    Raises:
        ArenaAlreadyClosedError: If ``closed_at`` is already set.
    """
    arena = await session.get(Arena, arena_id)
    if arena is None:
        return None
    if arena.closed_at is not None:
        raise ArenaAlreadyClosedError(arena_id)
    arena.closed_at = datetime.now(UTC)
    result = await session.execute(
        select(WorkAccountMetadata).where(WorkAccountMetadata.arena_id == arena_id)
    )
    meta = result.scalar_one_or_none()
    await session.flush()
    await session.refresh(arena)
    return arena, meta


async def list_arenas(
    session: AsyncSession,
    *,
    audience: Audience,
    domain: str,
    include_closed: bool = False,
) -> Sequence[Arena]:
    """Return arenas in a domain, optionally including closed ones.

    Args:
        session: Active async database session.
        audience: Documentary parameter for future visibility filtering.
        domain: Domain to scope the query.
        include_closed: If False (default), exclude arenas where closed_at IS NOT NULL.

    Returns:
        Sequence of :class:`Arena` rows ordered by ``created_at DESC``.
    """
    stmt = select(Arena).where(Arena.domain == domain)
    if not include_closed:
        stmt = stmt.where(Arena.closed_at.is_(None))
    stmt = stmt.order_by(Arena.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
