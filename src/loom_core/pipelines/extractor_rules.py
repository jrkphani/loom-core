"""Rules-based atom extractor.

All rule predicates receive (session, path, post) for signature uniformity.
Most rules don't consult the session; commitment-kind rules use it for
stakeholder email resolution. Uniform signature simplifies the dispatcher;
the unused-parameter smell on non-resolution rules is the deliberate trade.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path

import frontmatter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Atom, AtomCommitmentDetails, Stakeholder

RuleFn = Callable[[AsyncSession, Path, frontmatter.Post], Awaitable["Atom | None"]]


def _build_atom(
    kind: str,
    content: str,
) -> Atom:
    return Atom(
        id=str(ULID()),
        domain="work",
        type=kind,
        content=content,
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        extractor_provider="python_rules",
        extractor_skill_version="frontmatter-parser-v1",
        extraction_confidence=1.0,
        created_at=datetime.now(UTC),
    )


async def _frontmatter_decision_rule(
    session: AsyncSession, rel_path: Path, post: frontmatter.Post
) -> Atom | None:
    if post.metadata.get("type") == "decision":
        return _build_atom(
            kind="decision",
            content=post.content,
        )
    return None


async def _frontmatter_commitment_rule(
    session: AsyncSession, rel_path: Path, post: frontmatter.Post
) -> Atom | None:
    if post.metadata.get("type") == "commitment":
        owner_email = post.metadata.get("owner")
        owner_stakeholder_id = None
        if owner_email:
            result = await session.execute(
                select(Stakeholder.id).where(Stakeholder.primary_email == owner_email)
            )
            owner_stakeholder_id = result.scalar_one_or_none()

        due_raw = post.metadata.get("due")
        if isinstance(due_raw, datetime):
            due_date = due_raw.date()
        elif isinstance(due_raw, date):
            due_date = due_raw
        else:
            due_date = None

        atom = _build_atom(
            kind="commitment",
            content=post.content,
        )
        atom.commitment_details = AtomCommitmentDetails(
            atom_id=atom.id,
            owner_stakeholder_id=owner_stakeholder_id,
            due_date=due_date,
        )
        return atom
    return None


async def _extension_decision_rule(
    session: AsyncSession, rel_path: Path, post: frontmatter.Post
) -> Atom | None:
    if rel_path.name.endswith(".decision.md"):
        return _build_atom(
            kind="decision",
            content=post.content,
        )
    return None


async def _directory_decision_rule(
    session: AsyncSession, rel_path: Path, post: frontmatter.Post
) -> Atom | None:
    if "decisions" in rel_path.parts:
        return _build_atom(
            kind="decision",
            content=post.content,
        )
    return None


_FRONTMATTER_RULES: list[RuleFn] = [
    _frontmatter_decision_rule,
    _frontmatter_commitment_rule,
]
_EXTENSION_RULES: list[RuleFn] = [_extension_decision_rule]
_DIRECTORY_RULES: list[RuleFn] = [_directory_decision_rule]


async def process_file(session: AsyncSession, path: Path, *, vault_path: Path) -> list[Atom]:
    """Extract atoms using deterministic rules."""
    try:
        post = frontmatter.load(path)
    except Exception:
        # Frontmatter parse errors propagate.
        raise

    rel_path = path.relative_to(vault_path)

    for rule in _FRONTMATTER_RULES:
        atom = await rule(session, rel_path, post)
        if atom is not None:
            return [atom]

    for rule in _EXTENSION_RULES:
        atom = await rule(session, rel_path, post)
        if atom is not None:
            return [atom]

    for rule in _DIRECTORY_RULES:
        atom = await rule(session, rel_path, post)
        if atom is not None:
            return [atom]

    return []
