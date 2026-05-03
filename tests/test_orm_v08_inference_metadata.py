"""TDD: v0.8 inference metadata ORM columns.

B4: extractor_* on Atom, inference_* on HypothesisStateChange,
    composer_skill_version + provider_chain on BriefRun.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Arena, Atom, BriefRun, Event, Hypothesis, HypothesisStateChange
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def _svc_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    command.upgrade(Config("alembic.ini"), "head")
    return db_path


@pytest_asyncio.fixture
async def svc_session(_svc_test_db: Path) -> AsyncIterator[AsyncSession]:
    engine = create_engine(_svc_test_db)
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


async def test_atom_has_extractor_metadata_columns(svc_session: AsyncSession) -> None:
    """Atom must have 6 nullable extractor metadata columns with CHECK on provider."""
    ev = Event(id=str(ULID()), domain="work", type="process", occurred_at=datetime.now(UTC))
    svc_session.add(ev)
    await svc_session.flush()

    # All-populated
    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="decision",
        event_id=ev.id,
        content="Extracted decision",
        anchor_id="d-001",
        extractor_provider="claude_api",
        extractor_model_version="claude-sonnet-4-5",
        extractor_skill_version="atom-extractor-v1",
        extraction_confidence=0.85,
        source_span_start=12,
        source_span_end=78,
    )
    svc_session.add(atom)
    await svc_session.flush()
    await svc_session.refresh(atom)

    assert atom.extractor_provider == "claude_api"
    assert atom.extractor_model_version == "claude-sonnet-4-5"
    assert atom.extractor_skill_version == "atom-extractor-v1"
    assert atom.extraction_confidence == pytest.approx(0.85)
    assert atom.source_span_start == 12
    assert atom.source_span_end == 78

    # All nullable — insert without them
    atom2 = Atom(
        id=str(ULID()),
        domain="work",
        type="decision",
        event_id=ev.id,
        content="Manual decision",
        anchor_id="d-002",
    )
    svc_session.add(atom2)
    await svc_session.flush()
    await svc_session.refresh(atom2)

    assert atom2.extractor_provider is None
    assert atom2.extraction_confidence is None

    # CHECK on extractor_provider
    with pytest.raises(IntegrityError):
        atom3 = Atom(
            id=str(ULID()),
            domain="work",
            type="decision",
            event_id=ev.id,
            content="Bad provider",
            anchor_id="d-003",
            extractor_provider="invalid_provider",
        )
        svc_session.add(atom3)
        await svc_session.flush()
    await svc_session.rollback()


async def test_hypothesis_state_change_has_inference_metadata(
    svc_session: AsyncSession,
) -> None:
    """HypothesisStateChange must have 3 nullable inference metadata columns (no CHECK)."""
    arena = Arena(id=str(ULID()), domain="work", name="Test")
    svc_session.add(arena)
    hyp = Hypothesis(
        id=str(ULID()),
        domain="work",
        arena_id=arena.id,
        layer="arena",
        title="Test H",
    )
    svc_session.add(hyp)
    await svc_session.flush()

    hsc = HypothesisStateChange(
        id=str(ULID()),
        hypothesis_id=hyp.id,
        dimension="progress",
        old_value=None,
        new_value="in_delivery",
        changed_by="cron_inferred",
        inference_provider="claude_api",
        inference_model_version="claude-sonnet-4-5",
        inference_skill_version="state-infer-v1",
    )
    svc_session.add(hsc)
    await svc_session.flush()
    await svc_session.refresh(hsc)

    assert hsc.inference_provider == "claude_api"
    assert hsc.inference_model_version == "claude-sonnet-4-5"
    assert hsc.inference_skill_version == "state-infer-v1"

    # Nullable — omit them
    hsc2 = HypothesisStateChange(
        id=str(ULID()),
        hypothesis_id=hyp.id,
        dimension="confidence",
        old_value=None,
        new_value="high",
        changed_by="human_confirmed",
    )
    svc_session.add(hsc2)
    await svc_session.flush()
    await svc_session.refresh(hsc2)
    assert hsc2.inference_provider is None


async def test_brief_run_has_composer_metadata(svc_session: AsyncSession) -> None:
    """BriefRun must have composer_skill_version (Text) and provider_chain (JSON)."""
    chain = ["claude_api", "apple_fm"]
    br = BriefRun(
        id=str(ULID()),
        brief_type="engagement_daily",
        scope_type="engagement",
        scope_id=str(ULID()),
        output_path="outbox/work/briefs/eng_001.md",
        composer_skill_version="brief-composer-v2",
        provider_chain=chain,
    )
    svc_session.add(br)
    await svc_session.flush()
    await svc_session.refresh(br)

    assert br.composer_skill_version == "brief-composer-v2"
    assert br.provider_chain == chain

    # Nullable
    br2 = BriefRun(
        id=str(ULID()),
        brief_type="engagement_daily",
        scope_type="engagement",
        scope_id=str(ULID()),
        output_path="outbox/work/briefs/eng_002.md",
    )
    svc_session.add(br2)
    await svc_session.flush()
    await svc_session.refresh(br2)
    assert br2.composer_skill_version is None
    assert br2.provider_chain is None
