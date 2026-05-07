"""Atoms API endpoints (#013 atom lifecycle status + #016 search & provenance).

Routes:
- GET  /v1/atoms                          — list with filters (#016)
- GET  /v1/atoms/{atom_id}                — atom + nested source + kind details (#016)
- GET  /v1/atoms/{atom_id}/provenance     — atom + source + external refs (#016)
- POST /v1/atoms/{atom_id}/status         — write a lifecycle transition (#013)
- GET  /v1/atoms/{atom_id}/status/history — read the audit log (#013)
- PATCH /v1/atoms/{atom_id}/commitment    — patch commitment detail (#013)
- PATCH /v1/atoms/{atom_id}/risk          — patch risk detail (#013)

Inline exception handling per #013 DC11: typed service exceptions are caught
here and converted to HTTPException with the project's `{error, message}`
envelope.
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
    get_atom_provenance,
    get_atom_with_details,
    list_atom_status_history,
    list_atoms,
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


class AtomListItem(BaseModel):
    """List-item shape for GET /v1/atoms (DC4 — no nested source/details)."""

    id: str
    type: str
    domain: str
    content: str
    anchor_id: str
    confidence_sort_key: float | None
    dismissed: bool
    created_at: datetime
    event_id: str | None
    artifact_id: str | None

    model_config = ConfigDict(from_attributes=True)


class AtomListResponse(BaseModel):
    """Response model for GET /v1/atoms."""

    atoms: list[AtomListItem]


class AtomSource(BaseModel):
    """Nested source envelope for GET /:id and GET /:id/provenance (DC3).

    `kind = "event"` populates `type`, `source_path`, `body_summary`,
    `occurred_at`. `kind = "artifact"` populates `type` (from `type_tag`)
    and leaves `occurred_at` null.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event", "artifact"]
    id: str
    type: str | None
    source_path: str | None = None
    body_summary: str | None = None
    occurred_at: datetime | None = None


class CommitmentDetailBlock(BaseModel):
    """Detail block for commitment-kind atoms in GET /:id."""

    current_status: str
    due_date: date | None
    owner_stakeholder_id: str | None

    model_config = ConfigDict(from_attributes=True)


class AskDetailBlock(BaseModel):
    """Detail block for ask-kind atoms in GET /:id."""

    current_status: str
    due_date: date | None
    owner_stakeholder_id: str | None

    model_config = ConfigDict(from_attributes=True)


class RiskDetailBlock(BaseModel):
    """Detail block for risk-kind atoms in GET /:id (Pin 1: mitigation_status)."""

    mitigation_status: str
    severity: str
    owner_stakeholder_id: str | None

    model_config = ConfigDict(from_attributes=True)


AtomDetailUnion = CommitmentDetailBlock | AskDetailBlock | RiskDetailBlock


class AtomDetailResponse(BaseModel):
    """Response model for GET /v1/atoms/{atom_id} (DC4)."""

    id: str
    type: str
    domain: str
    content: str
    anchor_id: str
    confidence_sort_key: float | None
    dismissed: bool
    retracted: bool
    retracted_at: datetime | None
    created_at: datetime
    source: AtomSource
    details: AtomDetailUnion | None


class ExternalRefRead(BaseModel):
    """External reference in a provenance response."""

    id: str
    ref_type: str
    ref_value: str
    summary_md_path: str | None

    model_config = ConfigDict(from_attributes=True)


class ProvenanceResponse(BaseModel):
    """Response model for GET /v1/atoms/{atom_id}/provenance (DC4)."""

    atom_id: str
    content: str
    anchor_id: str
    source: AtomSource
    external_references: list[ExternalRefRead]


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


@router.get("/atoms", response_model=AtomListResponse)
async def get_atoms(
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
    domain: str | None = None,
    type: str | None = None,
    event_id: str | None = None,
    hypothesis_id: str | None = None,
    dismissed: bool = False,
) -> AtomListResponse:
    """List atoms with audience-scoped visibility filtering.

    Query params combine via AND (DC10). Default `dismissed=false` excludes
    dismissed rows; pass `?dismissed=true` to fetch only dismissed (DC1).
    Default ordering is `confidence_sort_key DESC, created_at DESC` (DC8).
    """
    rows = await list_atoms(
        session,
        audience=audience,
        domain=domain,
        atom_type=type,
        event_id=event_id,
        hypothesis_id=hypothesis_id,
        dismissed=dismissed,
    )
    return AtomListResponse(atoms=[AtomListItem.model_validate(a) for a in rows])


def _build_source(event: object | None, artifact: object | None) -> AtomSource:
    """Build the nested source envelope from a loaded Event or Artifact (DC3)."""
    if event is not None:
        return AtomSource(
            kind="event",
            id=event.id,  # type: ignore[attr-defined]
            type=event.type,  # type: ignore[attr-defined]
            source_path=event.source_path,  # type: ignore[attr-defined]
            body_summary=event.body_summary,  # type: ignore[attr-defined]
            occurred_at=event.occurred_at,  # type: ignore[attr-defined]
        )
    if artifact is not None:
        return AtomSource(
            kind="artifact",
            id=artifact.id,  # type: ignore[attr-defined]
            type=artifact.type_tag,  # type: ignore[attr-defined]
            source_path=None,
            body_summary=None,
            occurred_at=None,
        )
    # Atom CHECK constraint guarantees one of event_id/artifact_id is set.
    # This branch is defensive for type narrowing only.
    raise RuntimeError("atom has neither event nor artifact source")


_DETAIL_BLOCK_DISPATCH: dict[
    str, type[CommitmentDetailBlock | AskDetailBlock | RiskDetailBlock]
] = {
    "commitment": CommitmentDetailBlock,
    "ask": AskDetailBlock,
    "risk": RiskDetailBlock,
}


@router.get("/atoms/{atom_id}", response_model=AtomDetailResponse)
async def get_atom_by_id(
    atom_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> AtomDetailResponse:
    """Return atom + nested source envelope + kind-specific detail block.

    Detail block is null for `decision` and `status_update` kinds (DC7).
    Risk uses `mitigation_status`, not `current_status` — Pin 1 lock.
    """
    try:
        atom, event, artifact, detail = await get_atom_with_details(
            session, atom_id, audience=audience
        )
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        ) from exc

    detail_block: AtomDetailUnion | None = None
    block_cls = _DETAIL_BLOCK_DISPATCH.get(atom.type)
    if block_cls is not None and detail is not None:
        detail_block = block_cls.model_validate(detail)

    return AtomDetailResponse(
        id=atom.id,
        type=atom.type,
        domain=atom.domain,
        content=atom.content,
        anchor_id=atom.anchor_id,
        confidence_sort_key=atom.confidence_sort_key,
        dismissed=atom.dismissed,
        retracted=atom.retracted,
        retracted_at=atom.retracted_at,
        created_at=atom.created_at,
        source=_build_source(event, artifact),
        details=detail_block,
    )


@router.get("/atoms/{atom_id}/provenance", response_model=ProvenanceResponse)
async def get_atom_provenance_by_id(
    atom_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> ProvenanceResponse:
    """Return atom content + source envelope + linked external references.

    Visibility is enforced on the atom (DC6); source and external_references
    are returned unfiltered if the atom is visible.
    """
    try:
        atom, event, artifact, refs = await get_atom_provenance(session, atom_id, audience=audience)
    except AtomNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Atom {atom_id!r} not found"},
        ) from exc

    return ProvenanceResponse(
        atom_id=atom.id,
        content=atom.content,
        anchor_id=atom.anchor_id,
        source=_build_source(event, artifact),
        external_references=[ExternalRefRead.model_validate(r) for r in refs],
    )


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
