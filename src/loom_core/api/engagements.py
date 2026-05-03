"""Engagements API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Engagements.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_audience, get_session
from loom_core.storage.visibility import Audience
from loom_core.services.engagements import (
    ArenaNotFoundError,
    EngagementAlreadyClosedError,
    close_engagement,
    create_engagement,
    get_engagement,
    list_engagements,
    update_engagement,
)

router = APIRouter(tags=["engagements"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WorkEngagementMetadataRead(BaseModel):
    """Embedded work-domain engagement metadata in an engagement response."""

    sow_value: float | None
    sow_currency: str | None
    aws_funded: bool
    aws_program: str | None
    swim_lane: str | None

    model_config = {"from_attributes": True}


class WorkEngagementMetadataPatch(BaseModel):
    """Partial update for work-domain engagement metadata.

    swim_lane is typed as a Literal so Pydantic rejects invalid values
    at request-validation time (422), before any DB write occurs.
    """

    sow_value: float | None = None
    sow_currency: str | None = None
    aws_funded: bool | None = None
    aws_program: str | None = None
    swim_lane: (
        Literal[
            "p1_existing_customer",
            "p2_sales_generated",
            "p3_demand_gen_sdr",
            "p4_aws_referral",
        ]
        | None
    ) = None


class EngagementCreate(BaseModel):
    """Request body for POST /engagements."""

    domain: str
    arena_id: str
    name: str
    type_tag: str | None = None
    started_at: datetime | None = None


class EngagementPatch(BaseModel):
    """Request body for PATCH /engagements/:id — all fields optional.

    Note: ended_at is deliberately absent. Closing an engagement is done
    through the action endpoint POST /engagements/:id/close, which returns
    the open-hypotheses warning alongside the updated engagement.
    """

    name: str | None = None
    type_tag: str | None = None
    started_at: datetime | None = None
    work_metadata: WorkEngagementMetadataPatch | None = None


class EngagementCloseBody(BaseModel):
    """Optional request body for POST /engagements/:id/close."""

    force: bool = False
    override_reason: str | None = None

    @model_validator(mode="after")
    def require_override_reason_when_force(self) -> EngagementCloseBody:
        if self.force and not self.override_reason:
            raise ValueError("override_reason is required when force=True")
        return self


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
    work_metadata: WorkEngagementMetadataRead | None = None

    model_config = {"from_attributes": True}


class EngagementList(BaseModel):
    """Response model for GET /engagements."""

    data: list[EngagementRead]


class EngagementCloseResponse(BaseModel):
    """Response model for POST /engagements/:id/close."""

    engagement: EngagementRead
    warnings: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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
    audience: Annotated[Audience, Depends(get_audience)],
    domain: str = "work",
    arena_id: str | None = None,
    closed: bool | None = None,
) -> EngagementList:
    """List engagements with optional filters."""
    rows = await list_engagements(session, audience=audience, domain=domain, arena_id=arena_id, closed=closed)
    items = []
    for eng in rows:
        result = await get_engagement(session, eng.id, audience=audience)
        if result is None:
            continue
        e, meta = result
        read = EngagementRead.model_validate(e)
        read.work_metadata = WorkEngagementMetadataRead.model_validate(meta) if meta else None
        items.append(read)
    return EngagementList(data=items)


@router.get("/engagements/{engagement_id}", response_model=EngagementRead)
async def get_engagement_by_id(
    engagement_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> EngagementRead:
    """Get a single engagement by ID."""
    result = await get_engagement(session, engagement_id, audience=audience)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Engagement {engagement_id!r} not found",
            },
        )
    engagement, meta = result
    read = EngagementRead.model_validate(engagement)
    read.work_metadata = WorkEngagementMetadataRead.model_validate(meta) if meta else None
    return read


@router.patch("/engagements/{engagement_id}", response_model=EngagementRead)
async def patch_engagement(
    engagement_id: str,
    body: EngagementPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EngagementRead:
    """Partially update an engagement's name, type_tag, started_at, and/or work metadata."""
    result = await update_engagement(
        session,
        engagement_id,
        name=body.name,
        type_tag=body.type_tag,
        started_at=body.started_at,
        work_metadata=body.work_metadata,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Engagement {engagement_id!r} not found",
            },
        )
    engagement, meta = result
    read = EngagementRead.model_validate(engagement)
    read.work_metadata = WorkEngagementMetadataRead.model_validate(meta) if meta else None
    return read


@router.post("/engagements/{engagement_id}/close", response_model=EngagementCloseResponse)
async def close_engagement_endpoint(
    engagement_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    body: EngagementCloseBody | None = None,
) -> EngagementCloseResponse:
    """Soft-close an engagement by setting ended_at.

    Returns warnings if there are open hypotheses. When force=True and
    override_reason is provided, the close proceeds regardless of open
    hypotheses (override_reason is required with force=True).
    """
    close_body = body or EngagementCloseBody()
    try:
        result = await close_engagement(
            session,
            engagement_id,
            force=close_body.force,
            override_reason=close_body.override_reason,
        )
    except EngagementAlreadyClosedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "CONFLICT",
                "message": f"Engagement {engagement_id!r} is already closed",
            },
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Engagement {engagement_id!r} not found",
            },
        )
    engagement, meta, open_count = result
    read = EngagementRead.model_validate(engagement)
    read.work_metadata = WorkEngagementMetadataRead.model_validate(meta) if meta else None
    warnings: list[dict[str, Any]] = [{"open_hypotheses": open_count}] if open_count > 0 else []
    return EngagementCloseResponse(engagement=read, warnings=warnings)
