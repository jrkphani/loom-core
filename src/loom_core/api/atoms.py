"""Atoms API endpoints (#013 — atom lifecycle status).

Routes:
- POST /v1/atoms/{atom_id}/status
- GET  /v1/atoms/{atom_id}/status/history (B7)
- PATCH /v1/atoms/{atom_id}/commitment (B9)
- PATCH /v1/atoms/{atom_id}/risk (B10)

Inline exception handling per DC11: typed service exceptions are caught here
and converted to HTTPException with the project's `{error, message}` envelope.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_audience, get_session
from loom_core.services.atoms import (
    AtomKindMismatchError,
    AtomNotFoundError,
    AtomRetractedError,
    AtomStatusInvalidError,
    list_atom_status_history,
    update_atom_status,
    update_commitment_details,
    update_risk_details,
)
from loom_core.storage.visibility import Audience

router = APIRouter(tags=["atoms"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AtomStatusChangeCreate(BaseModel):
    """Request body for POST /atoms/{id}/status."""

    model_config = ConfigDict(extra="forbid")

    new_status: str
    changed_by: str = Field(min_length=1)
    reason: str | None = None


class AtomStatusChangeRead(BaseModel):
    """Response model for an atom_status_changes row."""

    id: str
    atom_id: str
    old_status: str | None
    new_status: str
    changed_at: datetime
    changed_by: str
    reason: str | None

    model_config = ConfigDict(from_attributes=True)


class AtomStatusHistoryEntry(BaseModel):
    """One change row in a status-history response."""

    old_status: str | None
    new_status: str
    changed_at: datetime
    changed_by: str
    reason: str | None

    model_config = ConfigDict(from_attributes=True)


class AtomStatusHistoryRead(BaseModel):
    """Response model for GET /atoms/{id}/status/history per DC8."""

    atom_id: str
    retracted_at: datetime | None
    changes: list[AtomStatusHistoryEntry]


class CommitmentPatch(BaseModel):
    """PATCH body for /atoms/{id}/commitment. At least one field required."""

    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None
    owner_stakeholder_id: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        # `model_fields_set` reports which fields the client actually sent —
        # distinguishes "not provided" from "explicitly null".
        if not self.model_fields_set:
            raise ValueError("at least one of due_date or owner_stakeholder_id required")
        return self


class CommitmentDetailsRead(BaseModel):
    """Response shape for the commitment detail row."""

    atom_id: str
    owner_stakeholder_id: str | None
    due_date: date | None
    current_status: str

    model_config = ConfigDict(from_attributes=True)


SeverityLiteral = Literal["low", "medium", "high", "critical"]


class RiskPatch(BaseModel):
    """PATCH body for /atoms/{id}/risk. At least one field required."""

    model_config = ConfigDict(extra="forbid")

    severity: SeverityLiteral | None = None
    owner_stakeholder_id: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one of severity or owner_stakeholder_id required")
        return self


class RiskDetailsRead(BaseModel):
    """Response shape for the risk detail row."""

    atom_id: str
    severity: str
    owner_stakeholder_id: str | None
    mitigation_status: str

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/atoms/{atom_id}/status", response_model=AtomStatusChangeRead)
async def post_atom_status(
    atom_id: str,
    body: AtomStatusChangeCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> AtomStatusChangeRead:
    """Apply a lifecycle status transition to a commitment / ask / risk atom.

    Writes the new status to the kind-specific detail-table column and appends
    an `atom_status_changes` audit row.
    """
    try:
        change = await update_atom_status(
            session,
            atom_id,
            audience=audience,
            new_status=body.new_status,
            changed_by=body.changed_by,
            reason=body.reason,
        )
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        ) from exc
    except AtomRetractedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ATOM_RETRACTED",
                "message": f"Atom {atom_id!r} is retracted; status changes are blocked",
            },
        ) from exc
    except AtomKindMismatchError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "ATOM_KIND_MISMATCH",
                "message": f"Atom kind {str(exc)!r} does not support lifecycle status",
            },
        ) from exc
    except AtomStatusInvalidError as exc:
        bad_status, kind = exc.args
        raise HTTPException(
            status_code=422,
            detail={
                "error": "ATOM_STATUS_INVALID",
                "message": f"Status {bad_status!r} is not valid for atom kind {kind!r}",
            },
        ) from exc
    return AtomStatusChangeRead.model_validate(change)


@router.get(
    "/atoms/{atom_id}/status/history",
    response_model=AtomStatusHistoryRead,
)
async def get_atom_status_history(
    atom_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> AtomStatusHistoryRead:
    """Return audit-log rows for an atom, ordered changed_at DESC.

    `retracted_at` is top-level so the UI can mark the timeline as unreliable
    when the atom has been retracted (per #013 v0.8 addendum).
    """
    try:
        atom, changes = await list_atom_status_history(session, atom_id, audience=audience)
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        ) from exc
    return AtomStatusHistoryRead(
        atom_id=atom.id,
        retracted_at=atom.retracted_at,
        changes=[AtomStatusHistoryEntry.model_validate(c) for c in changes],
    )


@router.patch(
    "/atoms/{atom_id}/commitment",
    response_model=CommitmentDetailsRead,
)
async def patch_atom_commitment(
    atom_id: str,
    body: CommitmentPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> CommitmentDetailsRead:
    """Update due_date and/or owner_stakeholder_id on a commitment atom's
    detail row. At least one field required."""
    sent = body.model_fields_set
    kwargs: dict[str, date | str | None] = {}
    if "due_date" in sent:
        kwargs["due_date"] = body.due_date
    if "owner_stakeholder_id" in sent:
        kwargs["owner_stakeholder_id"] = body.owner_stakeholder_id
    try:
        details = await update_commitment_details(session, atom_id, audience=audience, **kwargs)
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        ) from exc
    except AtomKindMismatchError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "ATOM_KIND_MISMATCH",
                "message": f"Atom kind {str(exc)!r} is not commitment",
            },
        ) from exc
    return CommitmentDetailsRead.model_validate(details)


@router.patch(
    "/atoms/{atom_id}/risk",
    response_model=RiskDetailsRead,
)
async def patch_atom_risk(
    atom_id: str,
    body: RiskPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> RiskDetailsRead:
    """Update severity and/or owner_stakeholder_id on a risk atom's detail row.
    At least one field required."""
    sent = body.model_fields_set
    kwargs: dict[str, str | None] = {}
    if "severity" in sent:
        kwargs["severity"] = body.severity
    if "owner_stakeholder_id" in sent:
        kwargs["owner_stakeholder_id"] = body.owner_stakeholder_id
    try:
        details = await update_risk_details(session, atom_id, audience=audience, **kwargs)
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        ) from exc
    except AtomKindMismatchError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "ATOM_KIND_MISMATCH",
                "message": f"Atom kind {str(exc)!r} is not risk",
            },
        ) from exc
    return RiskDetailsRead.model_validate(details)
