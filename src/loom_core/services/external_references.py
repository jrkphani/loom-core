"""External references service — create, query, and atom-linking operations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import (
    Atom,
    AtomExternalRef,
    ExternalReference,
)
from loom_core.storage.visibility import Audience, visibility_predicate


class AtomNotFoundError(Exception):
    """Raised when an atom_id does not match any atom row."""


class ExternalReferenceNotFoundError(Exception):
    """Raised when an external_ref_id does not match any external_references row."""


async def create_external_reference(
    session: AsyncSession,
    *,
    ref_type: str,
    ref_value: str,
    summary_md_path: str | None = None,
) -> tuple[ExternalReference, bool]:
    """Create or return an existing external reference.

    Returns:
        (ref, created) where created=True means a new row was inserted.
    """
    existing = (
        await session.execute(
            select(ExternalReference).where(
                ExternalReference.ref_type == ref_type,
                ExternalReference.ref_value == ref_value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    ref = ExternalReference(
        id=str(ULID()),
        ref_type=ref_type,
        ref_value=ref_value,
        summary_md_path=summary_md_path,
    )
    session.add(ref)
    await session.flush()
    await session.refresh(ref)
    return ref, True


async def get_external_reference(
    session: AsyncSession,
    ref_id: str,
    *,
    audience: Audience,
) -> ExternalReference | None:
    """Return an external reference by ID, or None if not found/visible."""
    stmt = (
        select(ExternalReference)
        .where(ExternalReference.id == ref_id)
        .where(
            visibility_predicate(
                ExternalReference.visibility_scope,
                "external_reference",
                ExternalReference.id,
                audience,
            )
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def link_atom_to_external_ref(
    session: AsyncSession,
    *,
    atom_id: str,
    external_ref_id: str,
) -> tuple[AtomExternalRef, bool]:
    """Link an atom to an external reference.

    Returns:
        (junction, created) where created=True means a new link was inserted.

    Raises:
        AtomNotFoundError: If atom_id does not match any atom.
        ExternalReferenceNotFoundError: If external_ref_id does not match any ref.
    """
    atom = await session.get(Atom, atom_id)
    if atom is None:
        raise AtomNotFoundError(atom_id)

    ref = await session.get(ExternalReference, external_ref_id)
    if ref is None:
        raise ExternalReferenceNotFoundError(external_ref_id)

    existing_junction = await session.get(AtomExternalRef, (atom_id, external_ref_id))
    if existing_junction is not None:
        return existing_junction, False

    junction = AtomExternalRef(atom_id=atom_id, external_ref_id=external_ref_id)
    session.add(junction)
    await session.flush()
    return junction, True


async def list_atom_external_refs(
    session: AsyncSession,
    atom_id: str,
    *,
    audience: Audience,
) -> Sequence[ExternalReference] | None:
    """Return all external references linked to an atom, ordered by captured_at DESC.

    Returns None if the atom does not exist or is not visible (route maps to 404).
    Filters out external references that are not visible to the audience.
    """
    atom_stmt = (
        select(Atom)
        .where(Atom.id == atom_id)
        .where(Atom.retracted.is_(False))
        .where(visibility_predicate(Atom.visibility_scope, "atom", Atom.id, audience))
    )
    atom = (await session.execute(atom_stmt)).scalar_one_or_none()
    if atom is None:
        return None

    stmt = (
        select(ExternalReference)
        .join(AtomExternalRef, AtomExternalRef.external_ref_id == ExternalReference.id)
        .where(AtomExternalRef.atom_id == atom_id)
        .where(
            visibility_predicate(
                ExternalReference.visibility_scope,
                "external_reference",
                ExternalReference.id,
                audience,
            )
        )
        .order_by(ExternalReference.captured_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()
