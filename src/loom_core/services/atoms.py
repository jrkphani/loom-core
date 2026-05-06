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
    Atom,
    AtomAskDetails,
    AtomCommitmentDetails,
    AtomRiskDetails,
    AtomStatusChange,
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
