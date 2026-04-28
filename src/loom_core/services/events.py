"""Event service — create and query events."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Event


async def create_event(
    session: AsyncSession,
    *,
    domain: str,
    event_type: str,
    occurred_at: datetime,
    source_path: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    body_summary: str | None = None,
) -> Event:
    """Create and persist a new event row.

    Returns:
        The newly created :class:`Event` instance with all fields populated.
    """
    event = Event(
        id=str(ULID()),
        domain=domain,
        type=event_type,
        occurred_at=occurred_at,
        source_path=source_path,
        source_metadata=source_metadata,
        body_summary=body_summary,
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return event


async def get_event(
    session: AsyncSession,
    event_id: str,
) -> Event | None:
    """Return an event by ID, or None if not found."""
    return await session.get(Event, event_id)


async def list_events(
    session: AsyncSession,
    *,
    domain: str,
    event_type: str | None = None,
) -> Sequence[Event]:
    """Return events in a domain ordered by occurred_at DESC.

    Args:
        session: Active async database session.
        domain: Domain to scope the query.
        event_type: Optional type filter.

    Returns:
        Sequence of :class:`Event` rows.
    """
    stmt = select(Event).where(Event.domain == domain)
    if event_type is not None:
        stmt = stmt.where(Event.type == event_type)
    stmt = stmt.order_by(Event.occurred_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
