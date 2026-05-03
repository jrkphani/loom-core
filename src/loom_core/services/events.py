"""Event service — create and query events."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Event
from loom_core.storage.visibility import Audience, visibility_predicate


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
    *,
    audience: Audience,
) -> Event | None:
    """Return an event by ID, filtered by visibility, or None if not found/visible."""
    stmt = (
        select(Event)
        .where(Event.id == event_id)
        .where(visibility_predicate(Event.visibility_scope, "event", Event.id, audience))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_events(
    session: AsyncSession,
    *,
    domain: str,
    audience: Audience,
    event_type: str | None = None,
) -> Sequence[Event]:
    """Return events in a domain ordered by occurred_at DESC.

    Args:
        session: Active async database session.
        domain: Domain to scope the query.
        audience: Who the query is for (drives visibility filtering).
        event_type: Optional type filter.

    Returns:
        Sequence of visible :class:`Event` rows.
    """
    stmt = (
        select(Event)
        .where(Event.domain == domain)
        .where(visibility_predicate(Event.visibility_scope, "event", Event.id, audience))
    )
    if event_type is not None:
        stmt = stmt.where(Event.type == event_type)
    stmt = stmt.order_by(Event.occurred_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
