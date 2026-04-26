"""Tests for the engagements API endpoints."""

from __future__ import annotations

from datetime import UTC

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def test_get_engagements_lists_with_filters(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/engagements supports domain, arena_id, and closed filters."""
    from datetime import datetime

    from sqlalchemy import update

    from loom_core.storage.models import Engagement

    # Setup: one arena, two engagements.
    arena_resp = await client.post("/v1/arenas", json={"domain": "work", "name": "Acme"})
    assert arena_resp.status_code == 201
    arena_id = arena_resp.json()["id"]

    e1_resp = await client.post(
        "/v1/engagements",
        json={"domain": "work", "arena_id": arena_id, "name": "Open Wave"},
    )
    assert e1_resp.status_code == 201
    e1_id = e1_resp.json()["id"]

    e2_resp = await client.post(
        "/v1/engagements",
        json={"domain": "work", "arena_id": arena_id, "name": "Closed Wave"},
    )
    assert e2_resp.status_code == 201
    e2_id = e2_resp.json()["id"]

    # Directly close the second engagement via DB (PATCH not in T1 scope).
    await db_session.execute(
        update(Engagement)
        .where(Engagement.id == e2_id)
        .values(ended_at=datetime(2026, 4, 1, tzinfo=UTC))
    )
    await db_session.commit()

    # GET all in domain → both returned.
    resp = await client.get("/v1/engagements?domain=work")
    assert resp.status_code == 200
    ids = {e["id"] for e in resp.json()["data"]}
    assert e1_id in ids
    assert e2_id in ids

    # GET filtered by arena_id → both in that arena.
    resp = await client.get(f"/v1/engagements?domain=work&arena_id={arena_id}")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2

    # GET closed=false → only the open engagement.
    resp = await client.get("/v1/engagements?domain=work&closed=false")
    assert resp.status_code == 200
    open_ids = {e["id"] for e in resp.json()["data"]}
    assert e1_id in open_ids
    assert e2_id not in open_ids


async def test_post_engagements_with_invalid_arena_returns_404(client: AsyncClient) -> None:
    """POST /v1/engagements with a non-existent arena_id returns 404."""
    response = await client.post(
        "/v1/engagements",
        json={
            "domain": "work",
            "arena_id": "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "name": "Wave 2",
        },
    )
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"] == "NOT_FOUND"


async def test_post_engagements_creates_row_under_arena(client: AsyncClient) -> None:
    """POST /v1/engagements returns 201 linked to a real arena."""
    # Create the parent arena first.
    arena_resp = await client.post(
        "/v1/arenas",
        json={"domain": "work", "name": "Panasonic"},
    )
    assert arena_resp.status_code == 201
    arena_id = arena_resp.json()["id"]

    response = await client.post(
        "/v1/engagements",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "name": "Wave 2",
            "type_tag": "delivery_wave",
            "started_at": "2026-04-01T00:00:00Z",
        },
    )
    assert response.status_code == 201

    data = response.json()
    assert len(data["id"]) == 26
    assert data["domain"] == "work"
    assert data["arena_id"] == arena_id
    assert data["name"] == "Wave 2"
    assert data["type_tag"] == "delivery_wave"
    assert data["started_at"] is not None
    assert data["ended_at"] is None
    assert "created_at" in data
