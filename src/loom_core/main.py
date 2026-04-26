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

from fastapi import FastAPI

from loom_core import __version__
from loom_core.api.health import router as health_router

# Process start time, used by /health for uptime reporting.
_START_TIME = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — initialise resources on startup, clean up on shutdown.

    Wire up here (in this order, when the relevant slices land):
      1. Config load
      2. Database engine + connection check
      3. APScheduler cron jobs
      4. Apple AI sidecar health probe (best-effort)
    """
    # TODO(W1): wire up config, database, scheduler.
    yield


app = FastAPI(
    title="Loom Core",
    version=__version__,
    description=(
        "Personal knowledge fabric — sole writer to SQLite and the Obsidian vault. "
        "Localhost-bound; no authentication in v1."
    ),
    lifespan=lifespan,
    # Strip the default /docs paths in production once auth lands. v1 leaves them on
    # because the daemon is localhost-only and they're useful for development.
)

# v1 path prefix. Breaking changes will go to /v2.
app.include_router(health_router, prefix="/v1")


def get_uptime_seconds() -> float:
    """Return seconds since this process started."""
    return time.monotonic() - _START_TIME
