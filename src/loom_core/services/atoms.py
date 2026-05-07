"""Atom service — read-side getter, lifecycle status transitions, history, patches.

Atoms expose four route surfaces (issue #013):
- POST /v1/atoms/{id}/status — write a lifecycle transition + audit row
- GET  /v1/atoms/{id}/status/history — read the audit log + retracted_at flag
- PATCH /v1/atoms/{id}/commitment — update due_date / owner_stakeholder_id
- PATCH /v1/atoms/{id}/risk — update severity / owner_stakeholder_id

Kind dispatch is explicit: each lifecycle-bearing atom kind has its own detail
table with a kind-specific column name for the lifecycle status. Pin 1 lock
(see #013 Implementation Notes): risk uses `mitigation_status`, not
`current_status`. Do not generalize the asymmetry away.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import (
    Artifact,
    Atom,
    AtomAskDetails,
    AtomCommitmentDetails,
    AtomExternalRef,
    AtomRiskDetails,
    AtomStatusChange,
    Event,
    ExternalReference,
)
from loom_core.storage.visibility import Audience, visibility_predicate


class AtomNotFoundError(Exception):
    """Raised when the atom doesn't exist or isn't visible to the audience."""


class AtomKindMismatchError(Exception):
    """Raised when the atom kind doesn't support the requested lifecycle operation."""


class AtomStatusInvalidError(Exception):
    """Raised when new_status isn't in the kind's CHECK enum.

    The first arg is the offending status string; the second is the kind.
    """


class AtomRetractedError(Exception):
    """Raised when a lifecycle operation is attempted on a retracted atom.

    Status transitions on retracted atoms are blocked at the read path; the
    user must un-retract first to record post-retraction state changes.
    """


# Lifecycle dispatch: per atom kind, the detail-table model class, the
# attribute name on that class that holds the lifecycle status, and the
# frozenset of valid statuses (mirrors the SQL CHECK constraint).
#
# Pin 1 lock: risk uses `mitigation_status`, NOT `current_status`. The
# asymmetry is explicit here on purpose — do not generalise it away.
_LifecycleDetailType = type[AtomCommitmentDetails] | type[AtomAskDetails] | type[AtomRiskDetails]
_LIFECYCLE_DISPATCH: dict[str, tuple[_LifecycleDetailType, str, frozenset[str]]] = {
    "commitment": (
        AtomCommitmentDetails,
        "current_status",
        frozenset({"open", "in_progress", "met", "slipped", "renegotiated", "cancelled"}),
    ),
    "ask": (
        AtomAskDetails,
        "current_status",
        frozenset({"raised", "acknowledged", "in_progress", "granted", "declined"}),
    ),
    "risk": (
        AtomRiskDetails,
        "mitigation_status",
        frozenset({"unmitigated", "mitigation_in_progress", "mitigated", "accepted"}),
    ),
}


async def get_atom(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
) -> Atom | None:
    """Return an atom by ID, filtered by visibility, or None if not found/visible."""
    stmt = (
        select(Atom)
        .where(Atom.id == atom_id)
        .where(visibility_predicate(Atom.visibility_scope, "atom", Atom.id, audience))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_atom_status(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
    new_status: str,
    changed_by: str,
    reason: str | None = None,
) -> AtomStatusChange:
    """Apply a lifecycle status transition and write an audit row.

    Dispatches on `atom.type`:
    - `commitment` → updates `atom_commitment_details.current_status`
    - (other kinds added in subsequent behaviours)

    Raises:
        AtomNotFoundError: atom doesn't exist or isn't visible.
        AtomKindMismatchError: atom kind has no lifecycle status.
    """
    atom = await get_atom(session, atom_id, audience=audience)
    if atom is None:
        raise AtomNotFoundError(atom_id)
    if atom.retracted:
        raise AtomRetractedError(atom_id)

    dispatch = _LIFECYCLE_DISPATCH.get(atom.type)
    if dispatch is None:
        raise AtomKindMismatchError(atom.type)
    detail_cls, status_attr, valid_statuses = dispatch

    if new_status not in valid_statuses:
        raise AtomStatusInvalidError(new_status, atom.type)

    details = (
        await session.execute(select(detail_cls).where(detail_cls.atom_id == atom_id))
    ).scalar_one()
    old_status = getattr(details, status_attr)
    setattr(details, status_attr, new_status)
    # Risk has no status_last_changed_at column; commitment + ask do.
    if hasattr(details, "status_last_changed_at"):
        details.status_last_changed_at = datetime.now(UTC)

    audit = AtomStatusChange(
        id=str(ULID()),
        atom_id=atom_id,
        old_status=old_status,
        new_status=new_status,
        changed_by=changed_by,
        reason=reason,
    )
    session.add(audit)
    await session.flush()
    await session.refresh(audit)
    return audit


_UNSET: object = object()


async def update_commitment_details(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
    due_date: date | None | object = _UNSET,
    owner_stakeholder_id: str | None | object = _UNSET,
) -> AtomCommitmentDetails:
    """Patch the commitment-detail row's `due_date` and/or `owner_stakeholder_id`.

    Sentinel `_UNSET` distinguishes "field not provided" from "field set to None".
    Caller passes only the fields the PATCH body included.

    Raises:
        AtomNotFoundError: atom doesn't exist or isn't visible.
        AtomKindMismatchError: atom kind isn't `commitment`.
    """
    atom = await get_atom(session, atom_id, audience=audience)
    if atom is None:
        raise AtomNotFoundError(atom_id)
    if atom.type != "commitment":
        raise AtomKindMismatchError(atom.type)

    details = (
        await session.execute(
            select(AtomCommitmentDetails).where(AtomCommitmentDetails.atom_id == atom_id)
        )
    ).scalar_one()
    if due_date is not _UNSET:
        details.due_date = due_date  # type: ignore[assignment]
    if owner_stakeholder_id is not _UNSET:
        details.owner_stakeholder_id = owner_stakeholder_id  # type: ignore[assignment]
    await session.flush()
    return details


async def update_risk_details(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
    severity: str | object = _UNSET,
    owner_stakeholder_id: str | None | object = _UNSET,
) -> AtomRiskDetails:
    """Patch the risk-detail row's `severity` and/or `owner_stakeholder_id`.

    Sentinel `_UNSET` distinguishes "field not provided" from "field set to None".

    Raises:
        AtomNotFoundError: atom doesn't exist or isn't visible.
        AtomKindMismatchError: atom kind isn't `risk`.
    """
    atom = await get_atom(session, atom_id, audience=audience)
    if atom is None:
        raise AtomNotFoundError(atom_id)
    if atom.type != "risk":
        raise AtomKindMismatchError(atom.type)

    details = (
        await session.execute(select(AtomRiskDetails).where(AtomRiskDetails.atom_id == atom_id))
    ).scalar_one()
    if severity is not _UNSET:
        details.severity = severity  # type: ignore[assignment]
    if owner_stakeholder_id is not _UNSET:
        details.owner_stakeholder_id = owner_stakeholder_id  # type: ignore[assignment]
    await session.flush()
    return details


async def list_atoms(
    session: AsyncSession,
    *,
    audience: Audience,
    domain: str | None = None,
    atom_type: str | None = None,
    event_id: str | None = None,
    hypothesis_id: str | None = None,
    dismissed: bool = False,
) -> Sequence[Atom]:
    """List atoms with audience-scoped visibility filtering.

    Filters AND together (DC10). Default dismissed=False (DC1). Default
    ordering is `confidence_sort_key DESC, created_at DESC` (DC8). Retracted
    atoms are NOT filtered here — that policy lives in #084 (DC2).

    Joining via `atom_attachments` for hypothesis_id (DC9). The bridge join
    column is `atom_id`; visibility on atoms still applies regardless.
    """
    stmt = select(Atom).where(
        visibility_predicate(Atom.visibility_scope, "atom", Atom.id, audience)
    )
    stmt = stmt.where(Atom.dismissed.is_(dismissed))
    if domain is not None:
        stmt = stmt.where(Atom.domain == domain)
    if atom_type is not None:
        stmt = stmt.where(Atom.type == atom_type)
    if event_id is not None:
        stmt = stmt.where(Atom.event_id == event_id)
    if hypothesis_id is not None:
        from loom_core.storage.models import AtomAttachment

        stmt = stmt.join(AtomAttachment, AtomAttachment.atom_id == Atom.id).where(
            AtomAttachment.hypothesis_id == hypothesis_id
        )
    stmt = stmt.order_by(Atom.confidence_sort_key.desc(), Atom.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


# Detail-block dispatch for GET /:id (DC7). Mirrors _LIFECYCLE_DISPATCH's
# explicit shape: kind → detail-table model class. Decision and status_update
# have no detail table; they yield None and the response carries `details: null`.
_DetailType = type[AtomCommitmentDetails] | type[AtomAskDetails] | type[AtomRiskDetails]
_DETAIL_DISPATCH: dict[str, _DetailType] = {
    "commitment": AtomCommitmentDetails,
    "ask": AtomAskDetails,
    "risk": AtomRiskDetails,
}


async def get_atom_with_details(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
) -> tuple[Atom, Event | None, Artifact | None, object | None]:
    """Return the atom, its source (event or artifact), and its kind detail row.

    Visibility-scoped on the atom (DC6). Source loaded without secondary
    visibility filtering — if the atom is visible, its provenance is visible.
    Detail dispatched on `atom.type` (DC7); decision and status_update return
    None for the detail block.

    Raises:
        AtomNotFoundError: atom doesn't exist or isn't visible.
    """
    atom = await get_atom(session, atom_id, audience=audience)
    if atom is None:
        raise AtomNotFoundError(atom_id)

    event: Event | None = None
    artifact: Artifact | None = None
    if atom.event_id is not None:
        event = (
            await session.execute(select(Event).where(Event.id == atom.event_id))
        ).scalar_one_or_none()
    elif atom.artifact_id is not None:
        artifact = (
            await session.execute(select(Artifact).where(Artifact.id == atom.artifact_id))
        ).scalar_one_or_none()

    detail: object | None = None
    detail_cls = _DETAIL_DISPATCH.get(atom.type)
    if detail_cls is not None:
        detail = (
            await session.execute(select(detail_cls).where(detail_cls.atom_id == atom_id))
        ).scalar_one_or_none()

    return atom, event, artifact, detail


async def get_atom_provenance(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
) -> tuple[Atom, Event | None, Artifact | None, Sequence[ExternalReference]]:
    """Return the atom, its source, and its linked external references.

    Visibility-scoped on the atom (DC6). Source and external_refs loaded
    without secondary visibility filtering.

    Raises:
        AtomNotFoundError: atom doesn't exist or isn't visible.
    """
    atom = await get_atom(session, atom_id, audience=audience)
    if atom is None:
        raise AtomNotFoundError(atom_id)

    event: Event | None = None
    artifact: Artifact | None = None
    if atom.event_id is not None:
        event = (
            await session.execute(select(Event).where(Event.id == atom.event_id))
        ).scalar_one_or_none()
    elif atom.artifact_id is not None:
        artifact = (
            await session.execute(select(Artifact).where(Artifact.id == atom.artifact_id))
        ).scalar_one_or_none()

    refs = (
        (
            await session.execute(
                select(ExternalReference)
                .join(
                    AtomExternalRef,
                    AtomExternalRef.external_ref_id == ExternalReference.id,
                )
                .where(AtomExternalRef.atom_id == atom_id)
            )
        )
        .scalars()
        .all()
    )

    return atom, event, artifact, refs


async def list_atom_status_history(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
) -> tuple[Atom, Sequence[AtomStatusChange]]:
    """Return the atom and its status-change rows ordered by changed_at DESC.

    Visibility-scoped: returns AtomNotFoundError if the atom is invisible to
    the audience. The returned atom carries `retracted_at` for the response
    envelope (DC8).
    """
    atom = await get_atom(session, atom_id, audience=audience)
    if atom is None:
        raise AtomNotFoundError(atom_id)
    rows = (
        (
            await session.execute(
                select(AtomStatusChange)
                .where(AtomStatusChange.atom_id == atom_id)
                .order_by(AtomStatusChange.changed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return atom, rows
