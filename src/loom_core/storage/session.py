"""SQLAlchemy 2.0 async engine + session factory.

We use `aiosqlite` as the async driver. WAL mode is enabled per system design
(concurrent reads alongside the single-writer Loom Core).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
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


def _set_sqlite_pragmas(dbapi_conn: Any, _: Any) -> None:
    """Enable WAL mode and foreign-key enforcement on every new connection."""
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


def create_engine(db_path: Path, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine configured for Loom Core's single-writer model.

    WAL mode and FK enforcement are wired on every connection via a SQLAlchemy
    event listener registered here, so callers need no additional setup.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent directory
            must exist.
        echo: If True, log every emitted SQL statement (debugging only).

    Returns:
        An `AsyncEngine` with WAL mode enabled and foreign keys enforced on
        every connection.
    """
    engine = create_async_engine(_build_url(db_path), echo=echo)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


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
