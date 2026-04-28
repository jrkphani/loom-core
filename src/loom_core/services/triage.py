"""Triage service — minimal write surface for #008. Extended in #019."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import TriageItem


async def create_triage_item(
    session: AsyncSession,
    *,
    item_type: str,
    related_entity_type: str,
    related_entity_id: str,
    context_summary: str | None = None,
    priority_score: float | None = None,
) -> TriageItem:
    """Create and persist a new triage_items row.

    The caller is responsible for passing a valid item_type. The CHECK
    constraint on the table will catch invalid values.

    Returns:
        The newly created :class:`TriageItem` with all fields populated.
    """
    item = TriageItem(
        id=str(ULID()),
        item_type=item_type,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        context_summary=context_summary,
        priority_score=priority_score,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item
