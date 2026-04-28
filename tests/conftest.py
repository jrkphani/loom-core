"""Shared test fixtures.

Conventions (per `../loom-meta/docs/PRD.md` §10.2):
- SQLite is real (in-memory or temp-file), never mocked.
- Apple AI sidecar is mocked at HTTP boundary (pytest-httpx).
- Claude API is mocked at SDK boundary.
- Time is injected and mock-able where cron / staleness matters.
- Loom's own modules are never mocked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.api._deps import get_session
from loom_core.main import app
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def _test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Apply migrations to a temp SQLite DB and return its path.

    This fixture is *synchronous* so ``alembic.command.upgrade`` can call
    ``asyncio.run()`` without conflicting with the running test event loop.
    It also sets ``LOOM_CONFIG_PATH`` (via monkeypatch) so that any code that
    reads the config sees the test database path.
    """
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')

    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    return db_path


@pytest_asyncio.fixture
async def client(_test_db: Path) -> AsyncIterator[AsyncClient]:
    """Async HTTP client bound to the FastAPI app with a live temp-file SQLite DB.

    ASGITransport does not trigger FastAPI's lifespan, so this fixture:
    - Creates its own async engine + session factory pointing at the test DB.
    - Overrides the ``get_session`` dependency so route handlers use the
      test session factory.
    - Exposes ``app.state.session_factory`` for the ``db_session`` fixture.

    Health tests use this fixture and continue to pass — the health endpoint
    does not query the database.
    """
    engine = create_engine(_test_db)
    factory = create_session_factory(engine)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session
    app.state.session_factory = factory

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.state.session_factory = None
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(client: AsyncClient) -> AsyncIterator[AsyncSession]:
    """Async session against the same temp DB that ``client`` targets.

    Depends on ``client`` so the engine and session factory are set up before
    this fixture is used.
    """
    async with app.state.session_factory() as session:
        yield session
