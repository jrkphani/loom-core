"""B1 (TDD): create_engine wires WAL + FK PRAGMA on every connection.

Without the listener the FK violation is silently ignored by SQLite.
After moving _set_sqlite_pragmas into create_engine, this test passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from loom_core.storage.session import create_engine


@pytest.mark.asyncio
async def test_create_engine_enforces_foreign_keys(tmp_path: Path) -> None:
    """create_engine must enforce FK constraints without any app bootstrap."""
    db_path = tmp_path / "fk_test.sqlite"
    engine = create_engine(db_path)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE parent (id TEXT PRIMARY KEY)"))
        await conn.execute(
            text(
                "CREATE TABLE child ("
                "  id TEXT PRIMARY KEY,"
                "  parent_id TEXT NOT NULL REFERENCES parent(id)"
                ")"
            )
        )
        # Insert orphan child — must raise with FK enforcement on.
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("INSERT INTO child (id, parent_id) VALUES ('c1', 'nonexistent')")
            )

    await engine.dispose()
