"""B2 (TDD): TzAwareDateTime preserves UTC timezone on SQLite round-trip.

SQLite's native DateTime type strips tzinfo. TzAwareDateTime stores as ISO
string with offset so the timezone survives the round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from loom_core.storage.session import create_engine, create_session_factory
from loom_core.storage.types import TzAwareDateTime


class _Base(DeclarativeBase):
    pass


class _TimestampedRow(_Base):
    __tablename__ = "tz_test_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime | None] = mapped_column(TzAwareDateTime(), nullable=True)


@pytest.mark.asyncio
async def test_tz_aware_datetime_round_trips_utc(tmp_path: Path) -> None:
    """TzAwareDateTime must return a tz-aware datetime after a SQLite round-trip."""
    db_path = tmp_path / "tz_test.sqlite"
    engine = create_engine(db_path)
    factory = create_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)

    now = datetime.now(UTC)
    async with factory() as session:
        session.add(_TimestampedRow(id=1, ts=now))
        await session.commit()

    async with factory() as session:
        row = (
            await session.execute(select(_TimestampedRow).where(_TimestampedRow.id == 1))
        ).scalar_one()

    assert row.ts is not None
    assert row.ts.tzinfo is not None, "tzinfo must survive the SQLite round-trip"
    # Allow 1-second tolerance for microsecond rounding differences.
    assert abs((row.ts - now).total_seconds()) < 1

    await engine.dispose()
