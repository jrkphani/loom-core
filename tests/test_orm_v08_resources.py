"""TDD: v0.8 resource ORM classes — Resource, ResourceAttribution, AssetUse.

B7 covers the three new tables added in migration §1.8.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import AssetUse, Resource, ResourceAttribution
from loom_core.storage.session import create_engine, create_session_factory


@pytest.fixture
def _svc_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    command.upgrade(Config("alembic.ini"), "head")
    return db_path


@pytest_asyncio.fixture
async def svc_session(_svc_test_db: Path) -> AsyncIterator[AsyncSession]:
    engine = create_engine(_svc_test_db)
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


async def test_resource_class(svc_session: AsyncSession) -> None:
    """Resource: 7-category enum + JSON quality_dimensions + inferred_from CHECK."""
    quality = {"focus_blocks_per_week": 6, "interruption_rate": "low"}
    res = Resource(
        id=str(ULID()),
        domain="work",
        category="time",
        name="Focus capacity",
        quantity=20.0,
        quantity_unit="hours_per_week",
        quality_dimensions=quality,
        window_start=date(2026, 1, 1),
        window_end=date(2026, 12, 31),
        inferred_from="calendar_density",
    )
    svc_session.add(res)
    await svc_session.flush()
    await svc_session.refresh(res)

    assert res.category == "time"
    assert res.quality_dimensions == quality
    assert res.visibility_scope == "private"

    # Invalid category
    with pytest.raises(IntegrityError):
        bad = Resource(
            id=str(ULID()),
            domain="work",
            category="invalid_category",
            name="bad",
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()

    # Invalid inferred_from
    with pytest.raises(IntegrityError):
        bad2 = Resource(
            id=str(ULID()),
            domain="work",
            category="time",
            name="bad2",
            inferred_from="invalid_source",
        )
        svc_session.add(bad2)
        await svc_session.flush()
    await svc_session.rollback()


async def test_resource_attribution_class(svc_session: AsyncSession) -> None:
    """ResourceAttribution: FK to resources + CHECK on consumer_type."""
    res = Resource(
        id=str(ULID()),
        domain="work",
        category="people",
        name="Madhavan attention",
    )
    svc_session.add(res)
    await svc_session.flush()

    ra = ResourceAttribution(
        id=str(ULID()),
        resource_id=res.id,
        consumer_type="hypothesis",
        consumer_id=str(ULID()),
        quantity=2.5,
        window_start=date(2026, 4, 1),
        window_end=date(2026, 4, 30),
    )
    svc_session.add(ra)
    await svc_session.flush()
    await svc_session.refresh(ra)

    assert ra.consumer_type == "hypothesis"
    assert ra.released_at is None

    # Invalid consumer_type
    with pytest.raises(IntegrityError):
        bad = ResourceAttribution(
            id=str(ULID()),
            resource_id=res.id,
            consumer_type="invalid_consumer",
            consumer_id=str(ULID()),
            quantity=1.0,
            window_start=date(2026, 1, 1),
            window_end=date(2026, 1, 31),
        )
        svc_session.add(bad)
        await svc_session.flush()
    await svc_session.rollback()


async def test_asset_use_class(svc_session: AsyncSession) -> None:
    """AssetUse: knowledge / tooling asset saturation tracking."""
    res = Resource(
        id=str(ULID()),
        domain="work",
        category="knowledge_asset",
        name="Panasonic Wave 2 case study",
    )
    svc_session.add(res)
    await svc_session.flush()

    au = AssetUse(
        id=str(ULID()),
        resource_id=res.id,
        audience_type="aws_partner",
        used_in_consumer_type="brief_run",
        used_in_consumer_id=str(ULID()),
    )
    svc_session.add(au)
    await svc_session.flush()
    await svc_session.refresh(au)

    assert au.audience_type == "aws_partner"
    assert au.used_at is not None
