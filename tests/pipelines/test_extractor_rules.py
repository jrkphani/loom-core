"""Tests for the rules-based atom extractor."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import frontmatter
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.pipelines.extractor_rules import process_file
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


async def test_no_match_returns_empty_list(tmp_path: Path, svc_session: AsyncSession) -> None:
    """A file with no recognized frontmatter, extension, or directory returns []."""
    test_file = tmp_path / "generic_note.md"
    test_file.write_text("Just some generic text.", encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert result == []


async def test_frontmatter_kind_decision_extracts_atom(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """A file with frontmatter type: decision is parsed as a decision atom."""
    test_file = tmp_path / "some_decision.md"
    post = frontmatter.Post("We decided to use SQLite.", **{"type": "decision"})
    test_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert len(result) == 1
    atom = result[0]
    assert atom.type == "decision"
    assert atom.extraction_confidence == 1.0
    assert atom.content == "We decided to use SQLite."
    assert len(atom.id) == 26
    assert atom.extractor_provider == "python_rules"
    assert atom.extractor_skill_version == "frontmatter-parser-v1"


@pytest.mark.parametrize(
    "case_name, owner_email, should_resolve",
    [
        ("Case A (resolved)", "alice@example.com", True),
        ("Case B (unresolved)", "bob@example.com", False),
    ],
)
async def test_frontmatter_kind_commitment_extracts_atom_with_details(
    tmp_path: Path,
    svc_session: AsyncSession,
    case_name: str,
    owner_email: str,
    should_resolve: bool,
) -> None:
    from datetime import date

    from ulid import ULID

    from loom_core.storage.models import Stakeholder

    seeded_id = None
    if should_resolve:
        seeded_id = str(ULID())
        stakeholder = Stakeholder(
            id=seeded_id,
            canonical_name="Alice",
            primary_email=owner_email,
        )
        svc_session.add(stakeholder)
        await svc_session.commit()

    test_file = tmp_path / "some_commitment.md"
    post = frontmatter.Post(
        "I will finish the design doc.",
        **{"type": "commitment", "owner": owner_email, "due": date(2026, 4, 26)},
    )
    test_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert len(result) == 1
    atom = result[0]
    assert atom.type == "commitment"
    assert atom.commitment_details is not None
    assert atom.commitment_details.due_date == date(2026, 4, 26)

    if should_resolve:
        assert atom.commitment_details.owner_stakeholder_id == seeded_id
    else:
        assert atom.commitment_details.owner_stakeholder_id is None


async def test_extension_pattern_falls_back_when_frontmatter_missing(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """A file matching .<kind>.md extension convention is parsed correctly."""
    test_file = tmp_path / "meeting.decision.md"
    test_file.write_text("We will launch in Q3.", encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert len(result) == 1
    assert result[0].type == "decision"
    assert result[0].content == "We will launch in Q3."


async def test_directory_convention_falls_back_when_extension_missing(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """A file in decisions/ is parsed as decision when frontmatter/extension missing."""
    decisions_dir = tmp_path / "decisions"
    decisions_dir.mkdir()
    test_file = decisions_dir / "foo.md"
    test_file.write_text("We chose PostgreSQL.", encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert len(result) == 1
    assert result[0].type == "decision"
    assert result[0].content == "We chose PostgreSQL."


async def test_frontmatter_wins_over_extension_and_directory(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """Conflicting signals respect tier order: frontmatter > extension > directory."""
    commitments_dir = tmp_path / "commitments"
    commitments_dir.mkdir()
    test_file = commitments_dir / "foo.commitment.md"

    post = frontmatter.Post("Frontmatter says this is a decision.", **{"type": "decision"})
    test_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert len(result) == 1
    assert result[0].type == "decision"
    assert result[0].content == "Frontmatter says this is a decision."


async def test_unknown_frontmatter_type_with_no_other_signals_returns_empty_list(
    tmp_path: Path, svc_session: AsyncSession
) -> None:
    """Unknown frontmatter type does not short-circuit, falls through to empty list if no other rules match."""
    test_file = tmp_path / "generic_note.md"
    post = frontmatter.Post("This is just a note.", **{"type": "garbage_kind"})
    test_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = await process_file(svc_session, test_file, vault_path=tmp_path)

    assert result == []
