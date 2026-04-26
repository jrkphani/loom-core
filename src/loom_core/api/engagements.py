"""Engagements API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Engagements.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.services.engagements import ArenaNotFoundError, create_engagement, list_engagements

router = APIRouter(tags=["engagements"])


class EngagementCreate(BaseModel):
    """Request body for POST /engagements."""

    domain: str
    arena_id: str
    name: str
    type_tag: str | None = None
    started_at: datetime | None = None


class EngagementRead(BaseModel):
    """Response model for a single engagement."""

    id: str
    domain: str
    arena_id: str
    name: str
    type_tag: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EngagementList(BaseModel):
    """Response model for GET /engagements."""

    data: list[EngagementRead]


@router.post("/engagements", response_model=EngagementRead, status_code=201)
async def post_engagements(
    body: EngagementCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EngagementRead:
    """Create a new engagement under a valid arena."""
    try:
        engagement = await create_engagement(
            session,
            domain=body.domain,
            arena_id=body.arena_id,
            name=body.name,
            type_tag=body.type_tag,
            started_at=body.started_at,
        )
    except ArenaNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Arena {body.arena_id!r} not found"},
        ) from exc
    return EngagementRead.model_validate(engagement)


@router.get("/engagements", response_model=EngagementList)
async def get_engagements(
    session: Annotated[AsyncSession, Depends(get_session)],
    domain: str = "work",
    arena_id: str | None = None,
    closed: bool | None = None,
) -> EngagementList:
    """List engagements with optional filters."""
    rows = await list_engagements(session, domain=domain, arena_id=arena_id, closed=closed)
    return EngagementList(data=[EngagementRead.model_validate(r) for r in rows])
