"""Claude/LLM-tier atom extractor.

Mirrors the rules-tier signature: `process_file(session, path, *, vault_path,
client) -> list[Atom]`. Session is read-only at extraction (used by
commitment-kind handling for stakeholder email lookup); caller owns persistence.
The LLM client is injected via the `ClaudeClient` Protocol so unit tests can
substitute a fake without engaging Anthropic SDK auth/retry/streaming layers.

Unlike the rules tier (which returns at most one atom per file), the LLM tier
may emit zero or more atoms per file. Each atom carries its own source span.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.llm.claude import ClaudeClient, ExtractedAtom
from loom_core.storage.models import Atom, AtomCommitmentDetails, Stakeholder

_EXTRACTOR_PROVIDER = "claude_api"
_EXTRACTOR_SKILL_VERSION = "prose-extraction-v1"


def _build_atom_skeleton(
    extracted: ExtractedAtom,
    *,
    extractor_model_version: str,
) -> Atom:
    return Atom(
        id=str(ULID()),
        domain="work",
        type=extracted.kind,
        content=extracted.content,
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        extractor_provider=_EXTRACTOR_PROVIDER,
        extractor_model_version=extractor_model_version,
        extractor_skill_version=_EXTRACTOR_SKILL_VERSION,
        extraction_confidence=extracted.extraction_confidence,
        source_span_start=extracted.source_span_start,
        source_span_end=extracted.source_span_end,
        created_at=datetime.now(UTC),
    )


async def _resolve_stakeholder_id(session: AsyncSession, owner_email: str | None) -> str | None:
    if owner_email is None:
        return None
    result = await session.execute(
        select(Stakeholder.id).where(Stakeholder.primary_email == owner_email)
    )
    return result.scalar_one_or_none()


async def _attach_commitment_details(
    session: AsyncSession, atom: Atom, extracted: ExtractedAtom
) -> None:
    owner_stakeholder_id = await _resolve_stakeholder_id(session, extracted.owner_email)
    due_date: date | None = extracted.due_date
    atom.commitment_details = AtomCommitmentDetails(
        atom_id=atom.id,
        owner_stakeholder_id=owner_stakeholder_id,
        due_date=due_date,
    )


async def process_file(
    session: AsyncSession,
    path: Path,
    *,
    vault_path: Path,
    client: ClaudeClient,
    extractor_model_version: str = "claude-sonnet-4-6",
) -> list[Atom]:
    """Extract atoms from `path` via the injected Claude client.

    Reads the file content, computes the vault-relative path, calls the
    client, and constructs Atom records. For commitment-kind atoms, performs
    extraction-time stakeholder resolution (exact match on email) and attaches
    `AtomCommitmentDetails` via the bidirectional relationship landed in #012.

    `extractor_model_version` is recorded on each atom for audit; defaults
    match the config default but the orchestrator may pass a different value.

    Caller owns persistence: this function does not call session.add/commit.
    `session.add_all(result)` cascades aux records via the relationship.
    """
    file_content = path.read_text(encoding="utf-8")
    rel_path = path.relative_to(vault_path)
    response = await client.extract_atoms(
        file_content=file_content,
        file_path_relative=rel_path.as_posix(),
    )

    atoms: list[Atom] = []
    for extracted in response.atoms:
        atom = _build_atom_skeleton(extracted, extractor_model_version=extractor_model_version)
        if extracted.kind == "commitment":
            await _attach_commitment_details(session, atom, extracted)
        atoms.append(atom)
    return atoms
