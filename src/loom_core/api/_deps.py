"""FastAPI dependency providers shared across route modules."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.storage.visibility import Audience


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession, committing on success or rolling back on error.

    Raises 503 if the database is not initialised (e.g. missing config during
    test bootstrap or first-start before migration).
    """
    factory = request.app.state.session_factory
    if factory is None:
        raise HTTPException(status_code=503, detail="Database not available")
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_audience(request: Request) -> Audience:
    """Derive the audience for the current request.

    Currently returns a self-audience. In the future, this will inspect the
    request identity/headers to construct the correct Audience instance for
    stakeholder-facing endpoints.
    """
    return Audience.for_self()
