"""Arenas API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Arenas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.services.arenas import create_arena

router = APIRouter(tags=["arenas"])


class ArenaCreate(BaseModel):
    """Request body for POST /arenas."""

    domain: str
    name: str
    description: str | None = None


class ArenaRead(BaseModel):
    """Response model for a single arena."""

    id: str
    domain: str
    name: str
    description: str | None
    closed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/arenas", response_model=ArenaRead, status_code=201)
async def post_arenas(
    body: ArenaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArenaRead:
    """Create a new arena."""
    arena = await create_arena(
        session,
        domain=body.domain,
        name=body.name,
        description=body.description,
    )
    return ArenaRead.model_validate(arena)
