"""Tests for the inbox sniffer pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import frontmatter
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.pipelines.sniffer import _CONFIDENCE_THRESHOLD, classify_file, process_file
from loom_core.storage.models import Event, TriageItem
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """A minimal vault directory structure under tmp_path."""
    for subdir in ("transcripts", "dictation", "emails", "notes"):
        (tmp_path / "inbox" / "work" / subdir).mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# B1 (DRIVER): .vtt in transcripts/ → type=process, confidence=1.0
# ---------------------------------------------------------------------------


def test_classify_file_transcripts_vtt_is_process_high_confidence(
    tmp_vault: Path,
) -> None:
    """A .vtt file in transcripts/ is classified as process with full confidence."""
    vtt = tmp_vault / "inbox" / "work" / "transcripts" / "sample.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello, world.",
        encoding="utf-8",
    )

    result = classify_file(vtt, vault_path=tmp_vault)

    assert result.file_type == "process"
    assert result.confidence == 1.0
    assert result.body_summary is not None
    assert "Hello" in result.body_summary


# ---------------------------------------------------------------------------
# B2 (DRIVER): dictation/ → type=inbox_derived, confidence=1.0
# ---------------------------------------------------------------------------


def test_classify_file_dictation_is_inbox_derived_high_confidence(
    tmp_vault: Path,
) -> None:
    """A file in dictation/ is classified as inbox_derived with full confidence."""
    memo = tmp_vault / "inbox" / "work" / "dictation" / "voice_memo.txt"
    memo.write_text("Quick thought about the steerco prep.", encoding="utf-8")

    result = classify_file(memo, vault_path=tmp_vault)

    assert result.file_type == "inbox_derived"
    assert result.confidence == 1.0
    assert result.body_summary is not None
    assert "steerco prep" in result.body_summary


# ---------------------------------------------------------------------------
# B3 (DRIVER): emails/ with type=email frontmatter → inbox_derived, 1.0
# ---------------------------------------------------------------------------


def test_classify_file_email_with_frontmatter_is_inbox_derived_high_confidence(
    tmp_vault: Path,
) -> None:
    """An email file with valid frontmatter is classified as inbox_derived at 1.0."""
    email_file = tmp_vault / "inbox" / "work" / "emails" / "msg.md"
    post = frontmatter.Post(
        "Hi, following up on the AWS RFP timeline...",
        **{
            "type": "email",
            "sender": "alice@example.com",
            "subject": "Re: SAP migration timeline",
        },
    )
    email_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = classify_file(email_file, vault_path=tmp_vault)

    assert result.file_type == "inbox_derived"
    assert result.confidence == 1.0
    assert result.body_summary is not None
    assert "AWS RFP" in result.body_summary
    assert result.source_metadata == {
        "type": "email",
        "sender": "alice@example.com",
        "subject": "Re: SAP migration timeline",
    }


# ---------------------------------------------------------------------------
# B4 (DRIVER): notes/ with type=note frontmatter → inbox_derived, 1.0
# ---------------------------------------------------------------------------


def test_classify_file_note_with_frontmatter_is_inbox_derived_high_confidence(
    tmp_vault: Path,
) -> None:
    """A note file with valid frontmatter is classified as inbox_derived at 1.0."""
    note_file = tmp_vault / "inbox" / "work" / "notes" / "quick.md"
    post = frontmatter.Post(
        "Madhavan said the steerco may slip a week.",
        **{"type": "note"},
    )
    note_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    result = classify_file(note_file, vault_path=tmp_vault)

    assert result.file_type == "inbox_derived"
    assert result.confidence == 1.0
    assert result.body_summary is not None
    assert "Madhavan" in result.body_summary
    assert result.source_metadata is not None
    assert result.source_metadata["type"] == "note"


# ---------------------------------------------------------------------------
# B5 (DRIVER): emails/ or notes/ with missing/invalid frontmatter → confidence=0.5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subdir,content,has_frontmatter",
    [
        ("emails", "Just plain text, no frontmatter at all.", False),
        (
            "notes",
            "---\ntype: garbage\n---\n\nSome note body here.",
            True,
        ),
    ],
)
def test_classify_file_in_emails_or_notes_without_valid_frontmatter_is_low_confidence(
    tmp_vault: Path,
    subdir: str,
    content: str,
    has_frontmatter: bool,
) -> None:
    """Files in emails/ or notes/ without valid frontmatter type get confidence=0.5."""
    fpath = tmp_vault / "inbox" / "work" / subdir / "unknown.md"
    fpath.write_text(content, encoding="utf-8")

    result = classify_file(fpath, vault_path=tmp_vault)

    assert result.file_type is None
    assert result.confidence == 0.5
    assert result.confidence < _CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# B6 (DRIVER): file outside recognized inbox subdirs → confidence=0.0
# ---------------------------------------------------------------------------


def test_classify_file_outside_inbox_dirs_is_unknown_zero_confidence(
    tmp_vault: Path,
) -> None:
    """A file not in any recognized inbox subdir gets file_type=None, confidence=0.0."""
    other_dir = tmp_vault / "inbox" / "work" / "random"
    other_dir.mkdir(parents=True)
    unknown = other_dir / "something.txt"
    unknown.write_text("Some content.", encoding="utf-8")

    result = classify_file(unknown, vault_path=tmp_vault)

    assert result.file_type is None
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# DB-bound fixtures (mirrored from test_events_service.py — not refactored
# into conftest per #008 scope discipline)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# B7 (DRIVER): process_file high-confidence → event_created
# ---------------------------------------------------------------------------


async def test_process_file_high_confidence_creates_event_returns_event_created(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """process_file on a high-confidence file creates an Event and returns event_created."""
    vtt = tmp_vault / "inbox" / "work" / "transcripts" / "foo.vtt"
    vtt.write_text("WEBVTT\n\nHello from the transcript.", encoding="utf-8")

    outcome = await process_file(svc_session, vtt, vault_path=tmp_vault)

    assert outcome.outcome == "event_created"
    assert outcome.event_id is not None
    assert outcome.triage_item_id is None

    rows = (await svc_session.execute(select(Event))).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.source_path == "inbox/work/transcripts/foo.vtt"
    assert event.type == "process"
    assert event.body_summary is not None
    assert event.occurred_at is not None


# ---------------------------------------------------------------------------
# B8 (DRIVER): process_file low-confidence → triage_item_created
# ---------------------------------------------------------------------------


async def test_process_file_low_confidence_creates_triage_item(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """process_file on a low-confidence file creates a TriageItem, no Event."""
    note = tmp_vault / "inbox" / "work" / "notes" / "garbage.md"
    note.write_text("Just plain text with no frontmatter.", encoding="utf-8")

    outcome = await process_file(svc_session, note, vault_path=tmp_vault)

    assert outcome.outcome == "triage_item_created"
    assert outcome.triage_item_id is not None
    assert outcome.event_id is None

    triage_rows = (await svc_session.execute(select(TriageItem))).scalars().all()
    assert len(triage_rows) == 1
    item = triage_rows[0]
    assert item.item_type == "ambiguous_routing"
    assert item.related_entity_type == "file"
    assert item.related_entity_id == "inbox/work/notes/garbage.md"
    assert item.priority_score == 0.5
    assert item.context_summary is not None

    event_rows = (await svc_session.execute(select(Event))).scalars().all()
    assert len(event_rows) == 0


# ---------------------------------------------------------------------------
# B9 (DRIVER): process_file detects duplicate source_path → skipped_duplicate
# ---------------------------------------------------------------------------


async def test_process_file_duplicate_source_path_returns_skipped_duplicate(
    tmp_vault: Path,
    svc_session: AsyncSession,
) -> None:
    """Second process_file call on same file returns skipped_duplicate with no new rows."""
    vtt = tmp_vault / "inbox" / "work" / "transcripts" / "foo.vtt"
    vtt.write_text("WEBVTT\n\nHello, world.", encoding="utf-8")

    outcome1 = await process_file(svc_session, vtt, vault_path=tmp_vault)
    assert outcome1.outcome == "event_created"
    event_id_1 = outcome1.event_id
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        outcome2 = await process_file(session2, vtt, vault_path=tmp_vault)
        await session2.commit()

    assert outcome2.outcome == "skipped_duplicate"
    assert outcome2.event_id == event_id_1
    assert outcome2.triage_item_id is None

    rows = (await svc_session.execute(select(Event))).scalars().all()
    assert len(rows) == 1
    triage_rows = (await svc_session.execute(select(TriageItem))).scalars().all()
    assert len(triage_rows) == 0
