"""FastAPI app entry for Loom Core.

Run in development:
    uv run uvicorn loom_core.main:app --reload --host 127.0.0.1 --port 9100

In production (launchd):
    uv run uvicorn loom_core.main:app --host 127.0.0.1 --port 9100 --workers 1

The single-worker constraint is intentional: Loom Core is the sole writer to
SQLite. Multiple workers would break write semantics under WAL mode.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy import event

from loom_core import __version__
from loom_core.api.arenas import router as arenas_router
from loom_core.api.engagements import router as engagements_router
from loom_core.api.health import router as health_router
from loom_core.config import load_settings
from loom_core.storage.session import create_engine, create_session_factory

# Process start time, used by /health for uptime reporting.
_START_TIME = time.monotonic()


def _set_sqlite_pragmas(dbapi_conn: Any, _: Any) -> None:
    """Enable WAL mode and foreign-key enforcement on every new connection."""
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialise resources on startup, clean up on shutdown.

    Wire up here (in this order, when the relevant slices land):
      1. Config load
      2. Database engine + connection check
      3. APScheduler cron jobs
      4. Apple AI sidecar health probe (best-effort)
    """
    settings = load_settings()
    db_path = settings.core.db_path

    engine = None
    try:
        engine = create_engine(db_path)
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
        app.state.session_factory = create_session_factory(engine)
    except Exception:
        # Tolerate missing / unconfigured DB (e.g. first boot before migration,
        # or test environments where LOOM_CONFIG_PATH is not set).
        app.state.session_factory = None

    yield

    app.state.session_factory = None
    if engine is not None:
        await engine.dispose()


app = FastAPI(
    title="Loom Core",
    version=__version__,
    description=(
        "Personal knowledge fabric — sole writer to SQLite and the Obsidian vault. "
        "Localhost-bound; no authentication in v1."
    ),
    lifespan=lifespan,
)

# v1 path prefix. Breaking changes will go to /v2.
app.include_router(health_router, prefix="/v1")
app.include_router(arenas_router, prefix="/v1")
app.include_router(engagements_router, prefix="/v1")


def get_uptime_seconds() -> float:
    """Return seconds since this process started."""
    return time.monotonic() - _START_TIME
