"""Events API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Events.
Events are immutable once written — no PATCH or DELETE handlers are registered.
FastAPI returns 405 automatically for unregistered methods on a known path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_audience, get_session
from loom_core.services.events import create_event, get_event, list_events
from loom_core.storage.visibility import Audience

router = APIRouter(tags=["events"])

EventTypeLiteral = Literal[
    "process",
    "inbox_derived",
    "state_change",
    "research",
    "publication",
    "external_reference",
]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class EventCreate(BaseModel):
    """Request body for POST /events."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    type: EventTypeLiteral
    occurred_at: datetime
    source_path: str | None = None
    source_metadata: dict[str, Any] | None = None
    body_summary: str | None = None


class EventRead(BaseModel):
    """Response model for a single event."""

    id: str
    domain: str
    type: EventTypeLiteral
    occurred_at: datetime
    source_path: str | None
    source_metadata: dict[str, Any] | None
    body_summary: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventList(BaseModel):
    """Response model for GET /events."""

    events: list[EventRead]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/events", response_model=EventRead, status_code=201)
async def post_events(
    body: EventCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventRead:
    """Create a new event."""
    event = await create_event(
        session,
        domain=body.domain,
        event_type=body.type,
        occurred_at=body.occurred_at,
        source_path=body.source_path,
        source_metadata=body.source_metadata,
        body_summary=body.body_summary,
    )
    return EventRead.model_validate(event)


@router.get("/events", response_model=EventList)
async def get_events(
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
    domain: str,
    type: EventTypeLiteral | None = None,
) -> EventList:
    """List events filtered by domain and optionally by type, ordered by occurred_at DESC."""
    rows = await list_events(session, domain=domain, audience=audience, event_type=type)
    return EventList(events=[EventRead.model_validate(r) for r in rows])


@router.get("/events/{event_id}", response_model=EventRead)
async def get_event_by_id(
    event_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> EventRead:
    """Get a single event by ID."""
    event = await get_event(session, event_id, audience=audience)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Event {event_id!r} not found"},
        )
    return EventRead.model_validate(event)
