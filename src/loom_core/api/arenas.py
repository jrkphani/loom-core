"""Arenas API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Arenas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_audience, get_session
from loom_core.storage.visibility import Audience
from loom_core.services.arenas import (
    ArenaAlreadyClosedError,
    close_arena,
    create_arena,
    get_arena,
    list_arenas,
    update_arena,
)

router = APIRouter(tags=["arenas"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WorkMetadataRead(BaseModel):
    """Embedded work-domain account metadata in an arena response."""

    industry: str | None
    region: str | None
    aws_segment: str | None
    customer_type: str | None

    model_config = {"from_attributes": True}


class WorkMetadataPatch(BaseModel):
    """Partial update for work-domain account metadata."""

    industry: str | None = None
    region: str | None = None
    aws_segment: str | None = None
    customer_type: str | None = None


class ArenaCreate(BaseModel):
    """Request body for POST /arenas."""

    domain: str
    name: str
    description: str | None = None


class ArenaPatch(BaseModel):
    """Request body for PATCH /arenas/:id — all fields optional."""

    name: str | None = None
    description: str | None = None
    work_metadata: WorkMetadataPatch | None = None


class ArenaRead(BaseModel):
    """Response model for a single arena."""

    id: str
    domain: str
    name: str
    description: str | None
    closed_at: datetime | None
    created_at: datetime
    work_metadata: WorkMetadataRead | None = None

    model_config = {"from_attributes": True}


class ArenaList(BaseModel):
    """Response model for GET /arenas."""

    arenas: list[ArenaRead]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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


@router.get("/arenas", response_model=ArenaList)
async def get_arenas(
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
    domain: str = "work",
    include_closed: bool = False,
) -> ArenaList:
    """List arenas, optionally including closed ones."""
    rows = await list_arenas(session, audience=audience, domain=domain, include_closed=include_closed)
    items = []
    for arena in rows:
        result = await get_arena(session, arena.id, audience=audience)
        if result is None:
            continue
        a, meta = result
        read = ArenaRead.model_validate(a)
        read.work_metadata = WorkMetadataRead.model_validate(meta) if meta else None
        items.append(read)
    return ArenaList(arenas=items)


@router.get("/arenas/{arena_id}", response_model=ArenaRead)
async def get_arena_by_id(
    arena_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> ArenaRead:
    """Get a single arena by ID."""
    result = await get_arena(session, arena_id, audience=audience)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Arena {arena_id!r} not found"},
        )
    arena, meta = result
    read = ArenaRead.model_validate(arena)
    read.work_metadata = WorkMetadataRead.model_validate(meta) if meta else None
    return read


@router.patch("/arenas/{arena_id}", response_model=ArenaRead)
async def patch_arena(
    arena_id: str,
    body: ArenaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArenaRead:
    """Partially update an arena's name, description, and/or work metadata."""
    result = await update_arena(
        session,
        arena_id,
        name=body.name,
        description=body.description,
        work_metadata=body.work_metadata,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Arena {arena_id!r} not found"},
        )
    arena, meta = result
    read = ArenaRead.model_validate(arena)
    read.work_metadata = WorkMetadataRead.model_validate(meta) if meta else None
    return read


@router.post("/arenas/{arena_id}/close", response_model=ArenaRead)
async def close_arena_endpoint(
    arena_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArenaRead:
    """Soft-close an arena by setting closed_at."""
    try:
        result = await close_arena(session, arena_id)
    except ArenaAlreadyClosedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "CONFLICT", "message": f"Arena {arena_id!r} is already closed"},
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Arena {arena_id!r} not found"},
        )
    arena, meta = result
    read = ArenaRead.model_validate(arena)
    read.work_metadata = WorkMetadataRead.model_validate(meta) if meta else None
    return read
