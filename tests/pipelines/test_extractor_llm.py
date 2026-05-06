"""Tests for the LLM-tier (Claude) atom extractor.

Mirrors the test patterns in test_extractor_rules.py: per-file svc_session fixture,
inline minimal-Stakeholder seeding, no LLM calls (FakeClaudeClient implements the
ClaudeClient Protocol).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.llm.claude import (
    AtomExtractionResponse,
    ClaudeClient,
)
from loom_core.main import app
from loom_core.pipelines.extractor_llm import process_file
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def _svc_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    return db_path


@pytest_asyncio.fixture
async def svc_session(_svc_test_db: Path) -> AsyncIterator[AsyncSession]:
    """Bare async session for pipeline integration tests."""
    engine = create_engine(_svc_test_db)
    factory = create_session_factory(engine)

    async def _override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    app.state.session_factory = factory

    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides.pop(get_session, None)
    app.state.session_factory = None
    await engine.dispose()


@dataclass
class _FakeCall:
    file_content: str
    file_path_relative: str


@dataclass
class FakeClaudeClient:
    """Implements the ClaudeClient Protocol for tests.

    Returns a canned response and records the args it was called with.
    """

    response: AtomExtractionResponse
    calls: list[_FakeCall] = field(default_factory=list)

    async def extract_atoms(
        self,
        *,
        file_content: str,
        file_path_relative: str,
    ) -> AtomExtractionResponse:
        self.calls.append(
            _FakeCall(file_content=file_content, file_path_relative=file_path_relative)
        )
        return self.response


# Static type-check assertion (mypy verifies FakeClaudeClient satisfies the Protocol).
_: ClaudeClient = FakeClaudeClient(response=AtomExtractionResponse(atoms=[]))


async def test_no_atoms_in_file_returns_empty_list(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """When the LLM returns zero atoms, process_file returns []."""
    test_file = tmp_path / "transcript.md"
    test_file.write_text("Some arbitrary content with no extractable facts.", encoding="utf-8")

    fake = FakeClaudeClient(response=AtomExtractionResponse(atoms=[]))

    result = await process_file(svc_session, test_file, vault_path=tmp_path, client=fake)

    assert result == []


async def test_decision_atom_extracted(tmp_path: Path, svc_session: AsyncSession) -> None:
    """A decision-kind ExtractedAtom is built into an Atom row with the
    correct type, content, confidence, source spans, and provenance fields."""
    from loom_core.llm.claude import ExtractedAtom

    test_file = tmp_path / "transcript.md"
    test_file.write_text("Decided to use SQLite over Postgres for v1.\n", encoding="utf-8")

    fake = FakeClaudeClient(
        response=AtomExtractionResponse(
            atoms=[
                ExtractedAtom(
                    kind="decision",
                    content="Decided to use SQLite over Postgres for v1.",
                    extraction_confidence=0.85,
                    source_span_start=10,
                    source_span_end=58,
                )
            ]
        )
    )

    result = await process_file(svc_session, test_file, vault_path=tmp_path, client=fake)

    assert len(result) == 1
    atom = result[0]
    assert atom.type == "decision"
    assert atom.content == "Decided to use SQLite over Postgres for v1."
    assert atom.extraction_confidence == 0.85
    assert atom.source_span_start == 10
    assert atom.source_span_end == 58
    assert atom.extractor_provider == "claude_api"
    assert atom.extractor_skill_version == "prose-extraction-v1"
    assert len(atom.id) == 26
    assert atom.anchor_id.startswith("^a-")


@pytest.mark.parametrize(
    "case_name, owner_email, should_resolve",
    [
        ("Case A (resolved)", "alice@example.com", True),
        ("Case B (unresolved)", "bob@example.com", False),
    ],
)
async def test_commitment_atom_extracted_with_stakeholder_resolution(
    tmp_path: Path,
    svc_session: AsyncSession,
    case_name: str,
    owner_email: str,
    should_resolve: bool,
) -> None:
    """A commitment-kind atom is built with `AtomCommitmentDetails`; stakeholder
    is resolved via Stakeholder.primary_email exact match (else NULL)."""
    from datetime import date

    from ulid import ULID

    from loom_core.llm.claude import ExtractedAtom
    from loom_core.storage.models import Stakeholder

    seeded_id: str | None = None
    if should_resolve:
        seeded_id = str(ULID())
        stakeholder = Stakeholder(
            id=seeded_id,
            canonical_name="Alice",
            primary_email=owner_email,
        )
        svc_session.add(stakeholder)
        await svc_session.commit()

    test_file = tmp_path / "transcript.md"
    test_file.write_text(f"{owner_email} will deliver the SOW by May 15.\n", encoding="utf-8")

    fake = FakeClaudeClient(
        response=AtomExtractionResponse(
            atoms=[
                ExtractedAtom(
                    kind="commitment",
                    content=f"{owner_email} will deliver the SOW by May 15.",
                    extraction_confidence=0.9,
                    source_span_start=0,
                    source_span_end=44,
                    owner_email=owner_email,
                    due_date=date(2026, 5, 15),
                )
            ]
        )
    )

    result = await process_file(svc_session, test_file, vault_path=tmp_path, client=fake)

    assert len(result) == 1
    atom = result[0]
    assert atom.type == "commitment"
    assert atom.commitment_details is not None
    assert atom.commitment_details.due_date == date(2026, 5, 15)

    if should_resolve:
        assert atom.commitment_details.owner_stakeholder_id == seeded_id
    else:
        assert atom.commitment_details.owner_stakeholder_id is None


async def test_multiple_atoms_extracted_from_single_file(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """One file → multiple atoms covering all 5 kinds.

    Locks the Pydantic Literal enum and the atom-construction path for each
    kind. Confirms commitment-only fields populate `commitment_details` while
    non-commitment kinds leave it None.
    """
    from datetime import date

    from ulid import ULID

    from loom_core.llm.claude import ExtractedAtom
    from loom_core.storage.models import Stakeholder

    # Seed a stakeholder so the commitment atom resolves.
    seeded_id = str(ULID())
    svc_session.add(
        Stakeholder(
            id=seeded_id,
            canonical_name="Alice",
            primary_email="alice@example.com",
        )
    )
    await svc_session.commit()

    test_file = tmp_path / "transcript.md"
    test_file.write_text("Long transcript content with multiple facts...\n", encoding="utf-8")

    fake = FakeClaudeClient(
        response=AtomExtractionResponse(
            atoms=[
                ExtractedAtom(
                    kind="decision",
                    content="Decided to use SQLite over Postgres for v1.",
                    extraction_confidence=0.85,
                    source_span_start=0,
                    source_span_end=43,
                ),
                ExtractedAtom(
                    kind="commitment",
                    content="Alice will deliver the SOW by May 15.",
                    extraction_confidence=0.9,
                    source_span_start=44,
                    source_span_end=82,
                    owner_email="alice@example.com",
                    due_date=date(2026, 5, 15),
                ),
                ExtractedAtom(
                    kind="ask",
                    content="Can the AWS team confirm the budget envelope?",
                    extraction_confidence=0.75,
                    source_span_start=83,
                    source_span_end=130,
                ),
                ExtractedAtom(
                    kind="risk",
                    content="Steerco may delay budget approval past May 30.",
                    extraction_confidence=0.7,
                    source_span_start=131,
                    source_span_end=178,
                ),
                ExtractedAtom(
                    kind="status_update",
                    content="Wave 2 design phase is on track.",
                    extraction_confidence=0.8,
                    source_span_start=179,
                    source_span_end=212,
                ),
            ]
        )
    )

    result = await process_file(svc_session, test_file, vault_path=tmp_path, client=fake)

    assert len(result) == 5
    assert [a.type for a in result] == [
        "decision",
        "commitment",
        "ask",
        "risk",
        "status_update",
    ]

    # Commitment atom has details with the resolved stakeholder.
    commitment = result[1]
    assert commitment.commitment_details is not None
    assert commitment.commitment_details.owner_stakeholder_id == seeded_id
    assert commitment.commitment_details.due_date == date(2026, 5, 15)

    # All other kinds have no commitment_details.
    for atom in (result[0], result[2], result[3], result[4]):
        assert atom.commitment_details is None

    # Each atom has a unique anchor_id.
    anchor_ids = {a.anchor_id for a in result}
    assert len(anchor_ids) == 5


async def test_extractor_passes_file_content_to_client(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """The extractor must pass the file's full content and the vault-relative
    path to the client. Catches the prompt-assembly failure mode where the
    file is opened but its content never reaches the LLM."""
    inbox_dir = tmp_path / "inbox" / "work" / "transcripts"
    inbox_dir.mkdir(parents=True)
    test_file = inbox_dir / "2026-04-19_panasonic-steerco.md"
    file_content = "Specific marker text the test will look for in fake.calls[0]."
    test_file.write_text(file_content, encoding="utf-8")

    fake = FakeClaudeClient(response=AtomExtractionResponse(atoms=[]))

    result = await process_file(svc_session, test_file, vault_path=tmp_path, client=fake)

    assert result == []
    assert len(fake.calls) == 1
    assert fake.calls[0].file_content == file_content
    assert fake.calls[0].file_path_relative == (
        "inbox/work/transcripts/2026-04-19_panasonic-steerco.md"
    )
