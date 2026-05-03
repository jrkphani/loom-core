"""Service-level tests for the events service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.services.events import create_event, get_event, list_events
from loom_core.storage.models import Event
from loom_core.storage.session import create_engine, create_session_factory
from loom_core.storage.visibility import Audience


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
    """Bare async session for service-level tests (no HTTP client needed)."""
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
# B1 (DRIVER): create_event happy path
# ---------------------------------------------------------------------------


async def test_create_event_persists_with_generated_id_and_created_at(
    svc_session: AsyncSession,
) -> None:
    """create_event returns a persisted Event with a generated ULID id and created_at."""
    event = await create_event(
        svc_session,
        domain="work",
        event_type="process",
        occurred_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
        source_path="inbox/work/transcripts/foo.vtt",
        source_metadata={"attendees": ["A", "B"]},
        body_summary="Steerco call with Madhavan.",
    )

    assert len(event.id) == 26
    assert event.created_at is not None
    assert event.type == "process"
    assert event.source_metadata is not None
    assert event.source_metadata["attendees"] == ["A", "B"]
    assert event.body_summary == "Steerco call with Madhavan."


# ---------------------------------------------------------------------------
# B2 (CONFIRMATION): all six event types are accepted; IDs are distinct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    ["process", "inbox_derived", "state_change", "research", "publication", "external_reference"],
)
async def test_create_event_accepts_all_six_types(
    svc_session: AsyncSession, event_type: str
) -> None:
    """Confirmation test: create_event is type-agnostic; all six schema types persist correctly.

    This confirms a property of the B1 implementation (the service passes type
    through without branching) rather than driving new code.
    """
    event = await create_event(
        svc_session,
        domain="work",
        event_type=event_type,
        occurred_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )
    assert event.type == event_type
    assert len(event.id) == 26


# ---------------------------------------------------------------------------
# B3 (DRIVER): list_events filters by domain and type, orders by occurred_at DESC
# ---------------------------------------------------------------------------


async def test_list_events_filters_and_orders_by_occurred_at_desc(
    svc_session: AsyncSession,
) -> None:
    """list_events returns events ordered by occurred_at DESC and supports type filter."""
    e1 = Event(
        id="01HW0000000000000000000001",
        domain="work",
        type="process",
        occurred_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
    )
    e2 = Event(
        id="01HW0000000000000000000002",
        domain="work",
        type="inbox_derived",
        occurred_at=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
    )
    e3 = Event(
        id="01HW0000000000000000000003",
        domain="work",
        type="process",
        occurred_at=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
    )
    for ev in [e1, e2, e3]:
        svc_session.add(ev)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        all_rows = await list_events(session2, domain="work", audience=Audience.for_self())

    assert len(all_rows) == 3
    assert [r.id for r in all_rows] == [e3.id, e2.id, e1.id]

    async with app.state.session_factory() as session3:
        process_rows = await list_events(
            session3, domain="work", audience=Audience.for_self(), event_type="process"
        )

    assert len(process_rows) == 2
    assert all(r.type == "process" for r in process_rows)
    assert [r.id for r in process_rows] == [e3.id, e1.id]


async def test_get_event_honours_audience(
    svc_session: AsyncSession,
) -> None:
    """get_event filters out events not visible to the audience."""
    e1 = Event(
        id="01HW0000000000000000000004",
        domain="work",
        type="process",
        occurred_at=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
        visibility_scope="private",
    )
    svc_session.add(e1)
    await svc_session.commit()

    async with app.state.session_factory() as session2:
        # Self audience sees private
        ev = await get_event(session2, e1.id, audience=Audience.for_self())
        assert ev is not None

        # Stakeholder audience does not see private
        sh_audience = Audience.for_stakeholders(["SH_1"])
        ev_sh = await get_event(session2, e1.id, audience=sh_audience)
        assert ev_sh is None
