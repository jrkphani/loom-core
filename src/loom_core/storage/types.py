"""Custom SQLAlchemy column types for Loom Core.

SQLite stores DateTime as a naive string by default, dropping timezone info.
TzAwareDateTime preserves the full ISO-8601 offset so round-trips are lossless.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

__all__ = ["TzAwareDateTime"]


class TzAwareDateTime(TypeDecorator[datetime]):
    """A DateTime type that preserves timezone info on SQLite.

    Stores the value as an ISO-8601 string with UTC offset (e.g.
    ``2026-05-03T14:30:00+00:00``). On read, reconstructs a tz-aware
    ``datetime`` via ``datetime.fromisoformat``.

    Use this in place of ``sa.DateTime`` for any column that must survive a
    SQLite round-trip with timezone intact.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)
