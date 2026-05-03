"""Visibility filter — canonical implementation per blueprint §6.4.

This module is the **single source of truth** for visibility filtering in
loom-core. Every read path that returns facts must apply visibility filtering
at the SQL level via ``visibility_predicate``; no service is permitted to
post-process visibility results.

Audience-filtered summarisation: atoms are filtered *before* reaching the
cognition layer, never after (see refactor plan §2.4).

Public exports:
  - ``Audience`` — frozen value type representing who the output is for.
  - ``visibility_predicate`` — builds a SQLAlchemy WHERE-clause expression.
  - ``derived_visibility`` — intersection rule for derived entity scopes.

Scope semantics (per blueprint §6.4):
  - ``domain_wide``      — visible to all readers in the domain.
  - ``engagement_scoped`` — visible only within the engagement's member set.
                            **Not handled by this library.** Engagement
                            membership requires a service-level JOIN to the
                            engagement roster. Callers compose an additional
                            WHERE clause for this scope (#078).
  - ``stakeholder_set``  — visible to a specific named set of stakeholders.
                            Handled here via count-match against
                            ``entity_visibility_members``. Subset direction:
                            ``audience ⊆ entity_members`` (every audience
                            member must appear in the entity's member set).
                            The reverse direction (entity_members ⊆ audience)
                            leaks: entity visible to {alice}, audience
                            {alice, bob} would incorrectly match.
  - ``private``          — visible only to self (the system owner). Never
                            matched for non-self audiences.

Why ``Audience.for_stakeholders([])`` raises:
  An empty audience would drive the count-match to ``0 == 0`` (always True),
  matching *every* ``stakeholder_set`` entity — a catastrophic data leak.
  Rejecting at construction eliminates this attack vector entirely.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select

from loom_core.storage.models import EntityVisibilityMember

# Ordered most-permissive → most-restrictive (index = restrictiveness rank).
_VISIBILITY_ORDER: tuple[str, ...] = (
    "domain_wide",
    "engagement_scoped",
    "stakeholder_set",
    "private",
)


@dataclass(frozen=True)
class Audience:
    """Who the output is for. Drives the visibility filter.

    Use the class-method factories rather than direct construction:
      - ``Audience.for_self()`` — the system owner; sees everything.
      - ``Audience.for_stakeholders(ids)`` — a named set of stakeholders.

    Frozen so instances are hashable and safe to use as dict keys or in sets.
    """

    stakeholder_ids: frozenset[str]
    is_self: bool = False

    @classmethod
    def for_self(cls) -> Audience:
        """Return an audience representing the system owner (sees all scopes)."""
        return cls(stakeholder_ids=frozenset(), is_self=True)

    @classmethod
    def for_stakeholders(cls, ids: Sequence[str]) -> Audience:
        """Return an audience for a specific named set of stakeholders.

        Args:
            ids: At least one stakeholder id. Raises ``ValueError`` if empty —
                 an empty set would match every ``stakeholder_set`` entity
                 via the ``0 == 0`` count-match (catastrophic leak).
        """
        if not ids:
            raise ValueError(
                "Audience.for_stakeholders requires at least one stakeholder id; "
                "an empty audience would match every stakeholder_set entity (leak risk)."
            )
        return cls(stakeholder_ids=frozenset(ids), is_self=False)


def visibility_predicate(
    visibility_col: Any,
    entity_type: str,
    entity_id_col: Any,
    audience: Audience,
) -> Any:
    """Build a SQLAlchemy WHERE-clause expression for visibility-aware queries.

    Usage::

        stmt = (
            select(Event)
            .where(visibility_predicate(
                Event.visibility_scope, "event", Event.id, audience,
            ))
        )

    Scope handling:
      - ``is_self`` audience: all four scopes match (``IN`` shorthand).
      - ``domain_wide``: always matches for non-self audiences.
      - ``stakeholder_set``: matches iff ``audience ⊆ entity_members``
        (count-match against ``entity_visibility_members``).
      - ``private``: never matches for non-self audiences.
      - ``engagement_scoped``: **not matched here**. Engagement membership
        requires a service-level JOIN; compose an additional WHERE clause
        at the call site (#078).

    Args:
        visibility_col: The ORM column for ``visibility_scope``
            (e.g. ``Event.visibility_scope``).
        entity_type: String discriminator matching the
            ``entity_visibility_members.entity_type`` CHECK enum
            (e.g. ``"event"``, ``"atom"``).
        entity_id_col: The ORM column for the entity's primary key
            (e.g. ``Event.id``).
        audience: Who the query is for. Required — choose explicitly.
    """
    if audience.is_self:
        return visibility_col.in_(_VISIBILITY_ORDER)

    # audience ⊆ entity_members:
    # Count how many of the audience's ids appear in entity_visibility_members
    # for this entity. If the count equals len(audience), every audience member
    # is in the member set → match. This is the safe direction: the reverse
    # (entity_members ⊆ audience) leaks when audience is a superset.
    stakeholder_set_match = (
        select(func.count(EntityVisibilityMember.stakeholder_id))
        .where(
            EntityVisibilityMember.entity_type == entity_type,
            EntityVisibilityMember.entity_id == entity_id_col,
            EntityVisibilityMember.stakeholder_id.in_(audience.stakeholder_ids),
        )
        .scalar_subquery()
    ) == len(audience.stakeholder_ids)

    return or_(
        visibility_col == "domain_wide",
        and_(
            visibility_col == "stakeholder_set",
            stakeholder_set_match,
        ),
    )


def derived_visibility(source_visibilities: Sequence[str]) -> str:
    """Return the most restrictive visibility scope from a list of sources.

    Per blueprint §6.4: derived facts inherit the INTERSECTION (most
    restrictive) of their source visibilities. Order from least to most
    restrictive: ``domain_wide`` < ``engagement_scoped`` < ``stakeholder_set``
    < ``private``.

    Args:
        source_visibilities: At least one scope string. Raises ``ValueError``
            if empty.
    """
    if not source_visibilities:
        raise ValueError("derived_visibility requires at least one source visibility")
    rank = {v: i for i, v in enumerate(_VISIBILITY_ORDER)}
    return max(source_visibilities, key=lambda v: rank[v])
