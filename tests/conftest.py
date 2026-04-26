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

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from loom_core.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTP client bound to the FastAPI app — no network, no real port.

    Uses ASGITransport so requests go directly into the app object.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
