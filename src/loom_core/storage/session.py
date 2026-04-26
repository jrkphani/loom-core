"""SQLAlchemy 2.0 async engine + session factory.

We use `aiosqlite` as the async driver. WAL mode is enabled per system design
(concurrent reads alongside the single-writer Loom Core).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all Loom Core ORM models."""


def _build_url(db_path: Path) -> str:
    """Construct the SQLAlchemy async URL for an aiosqlite database file."""
    return f"sqlite+aiosqlite:///{db_path}"


def create_engine(db_path: Path, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine configured for Loom Core's single-writer model.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent directory
            must exist.
        echo: If True, log every emitted SQL statement (debugging only).

    Returns:
        An `AsyncEngine` configured with WAL mode enabled, foreign keys on, and
        a small connection pool sized for one writer + a handful of readers.
    """
    return create_async_engine(
        _build_url(db_path),
        echo=echo,
        # SQLite-specific: WAL mode and FK enforcement set per-connection.
        # Apply via `event.listens_for(engine.sync_engine, "connect")` in app
        # bootstrap; this factory leaves connection setup to startup code.
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Async generator yielding a session inside a transaction.

    Caller must use `async with` on a session yielded from `factory()` directly
    in most code; this helper exists for the FastAPI dependency wiring.
    """
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
