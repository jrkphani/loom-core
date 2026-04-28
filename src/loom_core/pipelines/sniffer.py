"""Inbox sniffer — file detection, classification, and confidence routing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.services.events import create_event
from loom_core.services.triage import create_triage_item
from loom_core.storage.models import Event

# TODO(#019 or later): promote this threshold to config when there's a reason
# to vary it per environment.
_CONFIDENCE_THRESHOLD = 0.7

ClassifiedType = Literal["process", "inbox_derived"]


class FileClassification(BaseModel):
    """Result of classify_file: type, confidence, and optional parsed content."""

    file_type: ClassifiedType | None
    confidence: float
    body_summary: str | None = None
    source_metadata: dict[str, Any] | None = None


Outcome = Literal["event_created", "triage_item_created", "skipped_duplicate"]


class SniffOutcome(BaseModel):
    """Result of process_file: what action was taken and which entity was created."""

    outcome: Outcome
    event_id: str | None = None
    triage_item_id: str | None = None


async def process_file(
    session: AsyncSession,
    path: Path,
    *,
    vault_path: Path,
) -> SniffOutcome:
    """Orchestrate inbox file routing: classify, dedup, and write event or triage item.

    Async: performs DB lookups and writes via the provided session.
    source_path is stored vault-relative in POSIX form.
    occurred_at is always datetime.now(UTC).
    """
    source_path = path.relative_to(vault_path).as_posix()

    existing = (
        await session.execute(select(Event).where(Event.source_path == source_path).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return SniffOutcome(outcome="skipped_duplicate", event_id=existing.id)

    classification = classify_file(path, vault_path=vault_path)
    if classification.confidence >= _CONFIDENCE_THRESHOLD and classification.file_type is not None:
        event = await create_event(
            session,
            domain="work",
            event_type=classification.file_type,
            occurred_at=datetime.now(UTC),
            source_path=source_path,
            source_metadata=classification.source_metadata,
            body_summary=classification.body_summary,
        )
        return SniffOutcome(outcome="event_created", event_id=event.id)

    item = await create_triage_item(
        session,
        item_type="ambiguous_routing",
        related_entity_type="file",
        related_entity_id=source_path,
        context_summary=(
            f"Could not classify file at {source_path}: "
            f"file_type={classification.file_type}, "
            f"confidence={classification.confidence:.2f}."
        ),
        priority_score=classification.confidence,
    )
    return SniffOutcome(outcome="triage_item_created", triage_item_id=item.id)


def classify_file(path: Path, *, vault_path: Path) -> FileClassification:
    """Classify an inbox file by path and frontmatter.

    Synchronous: reads from filesystem only, no DB access.
    Returns FileClassification with confidence 0.0-1.0.
    """
    rel_parts = path.relative_to(vault_path).parts

    if "transcripts" in rel_parts:
        text = path.read_text(encoding="utf-8")
        return FileClassification(
            file_type="process",
            confidence=1.0,
            body_summary=text[:200].strip() or None,
            source_metadata=None,
        )

    if "dictation" in rel_parts:
        text = path.read_text(encoding="utf-8")
        return FileClassification(
            file_type="inbox_derived",
            confidence=1.0,
            body_summary=text[:200].strip() or None,
            source_metadata=None,
        )

    if "emails" in rel_parts:
        post = frontmatter.load(path)
        if post.metadata.get("type") == "email":
            return FileClassification(
                file_type="inbox_derived",
                confidence=1.0,
                body_summary=post.content[:200].strip() or None,
                source_metadata=post.metadata or None,
            )
        return FileClassification(
            file_type=None,
            confidence=0.5,
            body_summary=post.content[:200].strip() or None,
            source_metadata=post.metadata or None,
        )

    if "notes" in rel_parts:
        post = frontmatter.load(path)
        if post.metadata.get("type") == "note":
            return FileClassification(
                file_type="inbox_derived",
                confidence=1.0,
                body_summary=post.content[:200].strip() or None,
                source_metadata=post.metadata or None,
            )
        return FileClassification(
            file_type=None,
            confidence=0.5,
            body_summary=post.content[:200].strip() or None,
            source_metadata=post.metadata or None,
        )

    return FileClassification(
        file_type=None,
        confidence=0.0,
        body_summary=None,
        source_metadata=None,
    )
