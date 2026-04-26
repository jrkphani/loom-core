"""Tests for the arenas API endpoints."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.storage.models import Arena


async def test_post_arenas_creates_row(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /v1/arenas returns 201 and the row is persisted."""
    response = await client.post(
        "/v1/arenas",
        json={"domain": "work", "name": "Panasonic", "description": "Wave 2 customer"},
    )
    assert response.status_code == 201

    data = response.json()
    assert len(data["id"]) == 26
    assert data["domain"] == "work"
    assert data["name"] == "Panasonic"
    assert data["description"] == "Wave 2 customer"
    assert "created_at" in data

    # Read back from the database — proves the row was committed.
    result = await db_session.execute(select(Arena).where(Arena.id == data["id"]))
    arena = result.scalar_one()
    assert arena.id == data["id"]
    assert arena.name == "Panasonic"
