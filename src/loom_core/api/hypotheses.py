"""Hypotheses API endpoints.

API spec reference: `../loom-meta/docs/loom-api-v1.md` § Hypotheses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_audience, get_session
from loom_core.services.hypotheses import (
    ArenaNotFoundError,
    EngagementNotFoundError,
    HypothesisAlreadyClosedError,
    HypothesisNotTerminalError,
    InvalidOverrideReasonError,
    StateChangeProposalAlreadyResolvedError,
    StateChangeProposalNotFoundError,
    close_hypothesis,
    confirm_state_proposal,
    create_hypothesis,
    get_hypothesis,
    list_hypotheses,
    list_state_history,
    list_state_proposals,
    override_state_proposal,
    update_hypothesis,
)
from loom_core.storage.visibility import Audience

router = APIRouter(tags=["hypotheses"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

ProgressLiteral = Literal["proposed", "in_delivery", "realised", "confirmed", "dead"]
ConfidenceLiteral = Literal["low", "medium", "high"]
MomentumLiteral = Literal["accelerating", "steady", "slowing", "stalled"]


class HypothesisCreate(BaseModel):
    """Request body for POST /hypotheses."""

    domain: str
    arena_id: str
    engagement_id: str | None = None
    layer: Literal["arena", "engagement"]
    title: str
    description: str | None = None

    @model_validator(mode="after")
    def validate_layer_engagement_id(self) -> HypothesisCreate:
        if self.layer == "engagement" and self.engagement_id is None:
            raise ValueError("engagement_id is required when layer='engagement'")
        if self.layer == "arena" and self.engagement_id is not None:
            raise ValueError("engagement_id must be null when layer='arena'")
        return self


class HypothesisPatch(BaseModel):
    """Request body for PATCH /hypotheses/:id — title and description only.

    State fields (current_progress, current_confidence, current_momentum) are
    deliberately absent. State changes go through the state-change mechanism
    in #005/#006. extra='forbid' ensures callers can't sneak state fields in.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None


class HypothesisRead(BaseModel):
    """Response model for a single hypothesis."""

    id: str
    domain: str
    arena_id: str
    engagement_id: str | None
    layer: Literal["arena", "engagement"]
    title: str
    description: str | None
    current_progress: ProgressLiteral
    current_confidence: ConfidenceLiteral
    current_momentum: MomentumLiteral
    progress_last_changed_at: datetime | None
    confidence_last_reviewed_at: datetime | None
    momentum_last_reviewed_at: datetime | None
    confidence_inferred: bool
    momentum_inferred: bool
    created_at: datetime
    closed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class HypothesisList(BaseModel):
    """Response model for GET /hypotheses."""

    hypotheses: list[HypothesisRead]


class HypothesisStateChangeRead(BaseModel):
    """One row from hypothesis_state_changes."""

    id: str
    dimension: Literal["progress", "confidence", "momentum"]
    old_value: str | None
    new_value: str
    changed_at: datetime
    changed_by: Literal["cron_inferred", "human_confirmed", "human_overridden"]
    reasoning: str | None
    override_reason: str | None

    model_config = ConfigDict(from_attributes=True)


class HypothesisStateHistoryList(BaseModel):
    """Response model for GET /hypotheses/:id/state/history."""

    history: list[HypothesisStateChangeRead]


class StateChangeProposalRead(BaseModel):
    """One triage_items row of type state_change_proposal."""

    id: str
    item_type: Literal["state_change_proposal"]
    related_entity_type: str
    related_entity_id: str
    surfaced_at: datetime
    priority_score: float | None
    context_summary: str | None

    model_config = ConfigDict(from_attributes=True)


class StateChangeProposalList(BaseModel):
    """Response model for GET /hypotheses/:id/state/proposals."""

    proposals: list[StateChangeProposalRead]


_PROGRESS_VALUES: frozenset[str] = frozenset(
    {"proposed", "in_delivery", "realised", "confirmed", "dead"}
)
_CONFIDENCE_VALUES: frozenset[str] = frozenset({"low", "medium", "high"})
_MOMENTUM_VALUES: frozenset[str] = frozenset({"accelerating", "steady", "slowing", "stalled"})
_VALID_VALUES: dict[str, frozenset[str]] = {
    "progress": _PROGRESS_VALUES,
    "confidence": _CONFIDENCE_VALUES,
    "momentum": _MOMENTUM_VALUES,
}


class StateChangeConfirmRequest(BaseModel):
    """Request body for POST /hypotheses/:id/state/proposals/:pid/confirm."""

    model_config = ConfigDict(extra="forbid")

    dimension: Literal["progress", "confidence", "momentum"]
    new_value: str

    @model_validator(mode="after")
    def validate_new_value(self) -> StateChangeConfirmRequest:
        valid = _VALID_VALUES[self.dimension]
        if self.new_value not in valid:
            raise ValueError(
                f"new_value {self.new_value!r} is not valid for dimension {self.dimension!r}"
            )
        return self


class StateChangeOverrideRequest(BaseModel):
    """Request body for POST /hypotheses/:id/state/proposals/:pid/override."""

    model_config = ConfigDict(extra="forbid")

    dimension: Literal["progress", "confidence", "momentum"]
    new_value: str
    override_reason: str = ""  # min_length enforced by validator below

    @model_validator(mode="after")
    def validate_fields(self) -> StateChangeOverrideRequest:
        if len(self.override_reason) < 1:
            raise ValueError("override_reason is required")
        valid = _VALID_VALUES[self.dimension]
        if self.new_value not in valid:
            raise ValueError(
                f"new_value {self.new_value!r} is not valid for dimension {self.dimension!r}"
            )
        return self


class StateChangeResultRead(BaseModel):
    """Response model for confirm and override endpoints."""

    state_change_id: str
    hypothesis_id: str
    dimension: Literal["progress", "confidence", "momentum"]
    old_value: str | None
    new_value: str
    changed_at: datetime
    changed_by: Literal["human_confirmed", "human_overridden"]
    override_reason: str | None
    supporting_atoms: list[str]
    proposal_resolved: bool


class HypothesisStateRead(BaseModel):
    """Response model for GET /hypotheses/:id/state.

    Field names are bare (progress, confidence, momentum) per the API spec.
    ORM columns are prefixed with current_; constructed explicitly — no from_attributes.
    """

    progress: ProgressLiteral
    confidence: ConfidenceLiteral
    momentum: MomentumLiteral
    progress_last_changed_at: datetime | None
    confidence_last_reviewed_at: datetime | None
    momentum_last_reviewed_at: datetime | None
    confidence_inferred: bool
    momentum_inferred: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/hypotheses", response_model=HypothesisRead, status_code=201)
async def post_hypotheses(
    body: HypothesisCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisRead:
    """Create a new hypothesis."""
    try:
        hypothesis = await create_hypothesis(
            session,
            domain=body.domain,
            arena_id=body.arena_id,
            engagement_id=body.engagement_id,
            layer=body.layer,
            title=body.title,
            description=body.description,
        )
    except ArenaNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Arena {body.arena_id!r} not found"},
        ) from exc
    except EngagementNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Engagement {body.engagement_id!r} not found",
            },
        ) from exc
    return HypothesisRead.model_validate(hypothesis)


@router.get("/hypotheses", response_model=HypothesisList)
async def get_hypotheses(
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
    engagement_id: str | None = None,
    arena_id: str | None = None,
    layer: Literal["arena", "engagement"] | None = None,
) -> HypothesisList:
    """List hypotheses with filters. At least one of engagement_id or arena_id is required."""
    if engagement_id is None and arena_id is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "VALIDATION",
                "message": "At least one of engagement_id or arena_id is required.",
            },
        )
    rows = await list_hypotheses(
        session, audience=audience, engagement_id=engagement_id, arena_id=arena_id, layer=layer
    )
    return HypothesisList(hypotheses=[HypothesisRead.model_validate(h) for h in rows])


@router.get("/hypotheses/{hypothesis_id}", response_model=HypothesisRead)
async def get_hypothesis_by_id(
    hypothesis_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> HypothesisRead:
    """Get a single hypothesis by ID."""
    hypothesis = await get_hypothesis(session, hypothesis_id, audience=audience)
    if hypothesis is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Hypothesis {hypothesis_id!r} not found",
            },
        )
    return HypothesisRead.model_validate(hypothesis)


@router.get("/hypotheses/{hypothesis_id}/state", response_model=HypothesisStateRead)
async def get_hypothesis_state(
    hypothesis_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> HypothesisStateRead:
    """Return the current 3-dimensional state of a hypothesis."""
    hypothesis = await get_hypothesis(session, hypothesis_id, audience=audience)
    if hypothesis is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Hypothesis {hypothesis_id!r} not found",
            },
        )
    return HypothesisStateRead(
        progress=hypothesis.current_progress,
        confidence=hypothesis.current_confidence,
        momentum=hypothesis.current_momentum,
        progress_last_changed_at=hypothesis.progress_last_changed_at,
        confidence_last_reviewed_at=hypothesis.confidence_last_reviewed_at,
        momentum_last_reviewed_at=hypothesis.momentum_last_reviewed_at,
        confidence_inferred=hypothesis.confidence_inferred,
        momentum_inferred=hypothesis.momentum_inferred,
    )


@router.get("/hypotheses/{hypothesis_id}/state/history", response_model=HypothesisStateHistoryList)
async def get_hypothesis_state_history(
    hypothesis_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
    dimension: Literal["progress", "confidence", "momentum"] | None = None,
) -> HypothesisStateHistoryList:
    """Return the audit log of state changes for a hypothesis, ordered by changed_at DESC."""
    rows = await list_state_history(session, hypothesis_id, audience=audience, dimension=dimension)
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Hypothesis {hypothesis_id!r} not found",
            },
        )
    return HypothesisStateHistoryList(
        history=[HypothesisStateChangeRead.model_validate(r) for r in rows]
    )


@router.get("/hypotheses/{hypothesis_id}/state/proposals", response_model=StateChangeProposalList)
async def get_hypothesis_state_proposals(
    hypothesis_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    audience: Annotated[Audience, Depends(get_audience)],
) -> StateChangeProposalList:
    """Return pending state-change proposals (triage items) for a hypothesis."""
    # TODO(W5): add ?dimension filter once triage_items encodes dimension in context_summary
    rows = await list_state_proposals(session, hypothesis_id, audience=audience)
    if rows is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Hypothesis {hypothesis_id!r} not found",
            },
        )
    return StateChangeProposalList(
        proposals=[StateChangeProposalRead.model_validate(r) for r in rows]
    )


@router.post(
    "/hypotheses/{hypothesis_id}/state/proposals/{proposal_id}/confirm",
    response_model=StateChangeResultRead,
)
async def post_confirm_state_proposal(
    hypothesis_id: str,
    proposal_id: str,
    body: StateChangeConfirmRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StateChangeResultRead:
    """Confirm a pending state-change proposal as-is."""
    try:
        state_change = await confirm_state_proposal(
            session,
            hypothesis_id=hypothesis_id,
            proposal_id=proposal_id,
            dimension=body.dimension,
            new_value=body.new_value,
        )
    except StateChangeProposalNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Proposal {proposal_id!r} not found"},
        ) from exc
    except StateChangeProposalAlreadyResolvedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "CONFLICT",
                "message": f"Proposal {proposal_id!r} is already resolved",
            },
        ) from exc
    return StateChangeResultRead(
        state_change_id=state_change.id,
        hypothesis_id=hypothesis_id,
        dimension=state_change.dimension,
        old_value=state_change.old_value,
        new_value=state_change.new_value,
        changed_at=state_change.changed_at,
        changed_by=state_change.changed_by,
        override_reason=state_change.override_reason,
        supporting_atoms=[],
        proposal_resolved=True,
    )


@router.post(
    "/hypotheses/{hypothesis_id}/state/proposals/{proposal_id}/override",
    response_model=StateChangeResultRead,
)
async def post_override_state_proposal(
    hypothesis_id: str,
    proposal_id: str,
    body: StateChangeOverrideRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StateChangeResultRead:
    """Override a pending state-change proposal with a human-chosen value."""
    try:
        state_change = await override_state_proposal(
            session,
            hypothesis_id=hypothesis_id,
            proposal_id=proposal_id,
            dimension=body.dimension,
            new_value=body.new_value,
            override_reason=body.override_reason,
        )
    except StateChangeProposalNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": f"Proposal {proposal_id!r} not found"},
        ) from exc
    except StateChangeProposalAlreadyResolvedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "CONFLICT",
                "message": f"Proposal {proposal_id!r} is already resolved",
            },
        ) from exc
    except InvalidOverrideReasonError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "VALIDATION",
                "message": "override_reason must not be empty or whitespace-only",
            },
        ) from exc
    return StateChangeResultRead(
        state_change_id=state_change.id,
        hypothesis_id=hypothesis_id,
        dimension=state_change.dimension,
        old_value=state_change.old_value,
        new_value=state_change.new_value,
        changed_at=state_change.changed_at,
        changed_by=state_change.changed_by,
        override_reason=state_change.override_reason,
        supporting_atoms=[],
        proposal_resolved=True,
    )


@router.patch("/hypotheses/{hypothesis_id}", response_model=HypothesisRead)
async def patch_hypothesis(
    hypothesis_id: str,
    body: HypothesisPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisRead:
    """Update a hypothesis's title and/or description."""
    hypothesis = await update_hypothesis(
        session, hypothesis_id, title=body.title, description=body.description
    )
    if hypothesis is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Hypothesis {hypothesis_id!r} not found",
            },
        )
    return HypothesisRead.model_validate(hypothesis)


@router.post("/hypotheses/{hypothesis_id}/close", response_model=HypothesisRead)
async def close_hypothesis_endpoint(
    hypothesis_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisRead:
    """Close a hypothesis. Requires current_progress to be a terminal state."""
    try:
        hypothesis = await close_hypothesis(session, hypothesis_id)
    except HypothesisAlreadyClosedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "CONFLICT",
                "message": f"Hypothesis {hypothesis_id!r} is already closed",
            },
        ) from exc
    except HypothesisNotTerminalError as exc:
        progress = exc.args[0]
        raise HTTPException(
            status_code=422,
            detail={
                "error": "VALIDATION",
                "message": (
                    f"Hypothesis cannot be closed in state {progress!r};"
                    " current_progress must be 'realised', 'confirmed', or 'dead'."
                ),
            },
        ) from exc
    if hypothesis is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "NOT_FOUND",
                "message": f"Hypothesis {hypothesis_id!r} not found",
            },
        )
    return HypothesisRead.model_validate(hypothesis)
