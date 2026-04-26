"""Arena service — CRUD and business logic for arenas."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Arena


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
