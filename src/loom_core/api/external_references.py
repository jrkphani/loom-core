"""External references and atom-linking API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § External references.
POST /external-references and POST /atoms/{atom_id}/external-refs are idempotent:
  - duplicate (ref_type, ref_value) returns 200 with the existing row
  - duplicate atom-link returns 200 with the existing junction
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_audience, get_session
from loom_core.services.external_references import (
    AtomNotFoundError,
    ExternalReferenceNotFoundError,
    create_external_reference,
    get_external_reference,
    link_atom_to_external_ref,
    list_atom_external_refs,
)
from loom_core.storage.visibility import Audience

router = APIRouter(tags=["external_references"])

RefTypeLiteral = Literal["url", "email_msgid", "git_commit", "sharepoint", "gdrive"]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExternalReferenceCreate(BaseModel):
    """Request body for POST /external-references."""

    model_config = ConfigDict(extra="forbid")

    ref_type: RefTypeLiteral
    ref_value: str
    summary_md_path: str | None = None


class ExternalReferenceRead(BaseModel):
    """Response model for a single external reference."""

    id: str
    ref_type: RefTypeLiteral
    ref_value: str
    summary_md_path: str | None
    captured_at: datetime
    last_verified_at: datetime | None
    unreachable: bool

    model_config = ConfigDict(from_attributes=True)


class ExternalReferenceList(BaseModel):
    """Response model for GET /atoms/{id}/external-refs."""

    external_references: list[ExternalReferenceRead]


class AtomExternalRefLinkCreate(BaseModel):
    """Request body for POST /atoms/{atom_id}/external-refs."""

    model_config = ConfigDict(extra="forbid")

    external_ref_id: str


class AtomExternalRefLinkRead(BaseModel):
    """Response model for atom-to-external-ref junction."""

    atom_id: str
    external_ref_id: str

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/external-references", response_model=ExternalReferenceRead)
async def post_external_reference(
    body: ExternalReferenceCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExternalReferenceRead:
    """Create or return an existing external reference (idempotent on ref_type+ref_value)."""
    ref, created = await create_external_reference(
        session,
        ref_type=body.ref_type,
        ref_value=body.ref_value,
        summary_md_path=body.summary_md_path,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return ExternalReferenceRead.model_validate(ref)


@router.get("/external-references/{ref_id}", response_model=ExternalReferenceRead)
async def get_external_reference_by_id(
    ref_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> ExternalReferenceRead:
    """Get a single external reference by ID."""
    ref = await get_external_reference(session, ref_id, audience=audience)
    if ref is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"External reference {ref_id!r} not found"},
        )
    return ExternalReferenceRead.model_validate(ref)


@router.post("/atoms/{atom_id}/external-refs", response_model=AtomExternalRefLinkRead)
async def post_atom_external_ref_link(
    atom_id: str,
    body: AtomExternalRefLinkCreate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AtomExternalRefLinkRead:
    """Link an atom to an external reference (idempotent)."""
    try:
        junction, created = await link_atom_to_external_ref(
            session, atom_id=atom_id, external_ref_id=body.external_ref_id
        )
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom not found: {exc.args[0]}"},
        ) from exc
    except ExternalReferenceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"External reference not found: {exc.args[0]}",
            },
        ) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return AtomExternalRefLinkRead.model_validate(junction)


@router.get("/atoms/{atom_id}/external-refs", response_model=ExternalReferenceList)
async def get_atom_external_refs(
    atom_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> ExternalReferenceList:
    """Return all external references linked to an atom, ordered by captured_at DESC."""
    rows = await list_atom_external_refs(session, atom_id, audience=audience)
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        )
    return ExternalReferenceList(
        external_references=[ExternalReferenceRead.model_validate(r) for r in rows]
    )
