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


async def test_get_engagement_by_id_returns_engagement(client: AsyncClient) -> None:
    """GET /v1/engagements/:id returns 200 with engagement fields + null work_metadata."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    assert arena_r.status_code == 201
    arena_id = arena_r.json()["id"]

    eng_r = await client.post(
        "/v1/engagements",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "name": "Wave 1",
            "type_tag": "delivery_wave",
        },
    )
    assert eng_r.status_code == 201
    eng_id = eng_r.json()["id"]

    resp = await client.get(f"/v1/engagements/{eng_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == eng_id
    assert data["domain"] == "work"
    assert data["arena_id"] == arena_id
    assert data["name"] == "Wave 1"
    assert data["ended_at"] is None
    assert "created_at" in data
    assert "work_metadata" in data
    assert data["work_metadata"] is None


async def test_get_engagement_by_id_not_found_returns_404(client: AsyncClient) -> None:
    """GET /v1/engagements/:id with unknown id returns 404 with error envelope."""
    resp = await client.get("/v1/engagements/01HXXXXXXXXXXXXXXXXXXXXXXX")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NOT_FOUND"


async def test_get_engagement_returns_work_metadata_when_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/engagements/:id returns populated work_metadata when a row exists."""
    from loom_core.storage.models import WorkEngagementMetadata

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    meta = WorkEngagementMetadata(
        engagement_id=eng_id,
        sow_value=500000.0,
        sow_currency="SGD",
        aws_funded=True,
        aws_program="MAP",
        swim_lane="p1_existing_customer",
    )
    db_session.add(meta)
    await db_session.commit()

    resp = await client.get(f"/v1/engagements/{eng_id}")
    assert resp.status_code == 200
    wm = resp.json()["work_metadata"]
    assert wm is not None
    assert wm["sow_value"] == 500000.0
    assert wm["sow_currency"] == "SGD"
    assert wm["aws_funded"] is True
    assert wm["aws_program"] == "MAP"
    assert wm["swim_lane"] == "p1_existing_customer"


async def test_patch_engagement_updates_core_fields(client: AsyncClient) -> None:
    """PATCH /v1/engagements/:id updates name, type_tag, started_at; persists."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "name": "Wave 1",
            "type_tag": "delivery_wave",
        },
    )
    eng_id = eng_r.json()["id"]

    patch_resp = await client.patch(
        f"/v1/engagements/{eng_id}",
        json={
            "name": "Wave 1 Renamed",
            "type_tag": "delivery_wave_pilot",
            "started_at": "2026-04-01T00:00:00Z",
        },
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["name"] == "Wave 1 Renamed"
    assert data["type_tag"] == "delivery_wave_pilot"
    assert data["started_at"] is not None
    assert data["ended_at"] is None  # close action only — not PATCH

    get_resp = await client.get(f"/v1/engagements/{eng_id}")
    assert get_resp.json()["name"] == "Wave 1 Renamed"


async def test_patch_engagement_updates_work_metadata(client: AsyncClient) -> None:
    """PATCH upserts work_metadata; partial second patch preserves existing fields."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    r1 = await client.patch(
        f"/v1/engagements/{eng_id}",
        json={
            "work_metadata": {
                "sow_value": 500000.0,
                "sow_currency": "SGD",
                "aws_funded": True,
                "aws_program": "MAP",
                "swim_lane": "p1_existing_customer",
            }
        },
    )
    assert r1.status_code == 200
    wm = r1.json()["work_metadata"]
    assert wm["sow_value"] == 500000.0
    assert wm["aws_program"] == "MAP"

    r2 = await client.patch(
        f"/v1/engagements/{eng_id}",
        json={"work_metadata": {"aws_program": "PBP"}},
    )
    assert r2.status_code == 200
    wm2 = r2.json()["work_metadata"]
    assert wm2["aws_program"] == "PBP"
    assert wm2["sow_value"] == 500000.0  # preserved
    assert wm2["sow_currency"] == "SGD"  # preserved

    get_r = await client.get(f"/v1/engagements/{eng_id}")
    assert get_r.json()["work_metadata"]["aws_program"] == "PBP"
    assert get_r.json()["work_metadata"]["sow_value"] == 500000.0


async def test_patch_engagement_with_invalid_swim_lane_returns_422(client: AsyncClient) -> None:
    """PATCH with invalid swim_lane value returns 422 from Pydantic validation."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    resp = await client.patch(
        f"/v1/engagements/{eng_id}",
        json={"work_metadata": {"swim_lane": "p99_invalid"}},
    )
    assert resp.status_code == 422


async def test_post_engagement_close_with_no_open_hypotheses_returns_empty_warnings(
    client: AsyncClient,
) -> None:
    """POST /v1/engagements/:id/close with no hypotheses returns 200 with empty warnings."""
    from datetime import UTC, datetime

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    before = datetime.now(UTC)
    close_resp = await client.post(f"/v1/engagements/{eng_id}/close")
    assert close_resp.status_code == 200

    body = close_resp.json()
    assert body["warnings"] == []
    assert body["engagement"]["ended_at"] is not None
    ended_at = datetime.fromisoformat(body["engagement"]["ended_at"])
    assert ended_at >= before.replace(tzinfo=None)

    get_resp = await client.get(f"/v1/engagements/{eng_id}")
    assert get_resp.json()["ended_at"] is not None


async def test_post_engagement_close_with_open_hypotheses_returns_warnings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/engagements/:id/close with open hypotheses returns warnings with count."""
    from ulid import ULID

    from loom_core.storage.models import Hypothesis

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    # Insert two open hypotheses directly via db_session.
    for _ in range(2):
        h = Hypothesis(
            id=str(ULID()),
            domain="work",
            arena_id=arena_id,
            engagement_id=eng_id,
            layer="engagement",
            title="Test hypothesis",
            current_progress="proposed",
            current_confidence="medium",
            current_momentum="steady",
            confidence_inferred=True,
            momentum_inferred=True,
        )
        db_session.add(h)
    await db_session.commit()

    close_resp = await client.post(f"/v1/engagements/{eng_id}/close")
    assert close_resp.status_code == 200

    body = close_resp.json()
    assert body["warnings"] == [{"open_hypotheses": 2}]
    assert body["engagement"]["ended_at"] is not None


async def test_post_engagement_close_on_closed_engagement_returns_409(
    client: AsyncClient,
) -> None:
    """POST /v1/engagements/:id/close on an already-ended engagement returns 409."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    first = await client.post(f"/v1/engagements/{eng_id}/close")
    assert first.status_code == 200

    second = await client.post(f"/v1/engagements/{eng_id}/close")
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "CONFLICT"


async def test_post_engagement_close_with_force_closes_with_open_hypotheses(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Force-close with override_reason succeeds even with open hypotheses."""
    from ulid import ULID

    from loom_core.storage.models import Hypothesis

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    h = Hypothesis(
        id=str(ULID()),
        domain="work",
        arena_id=arena_id,
        engagement_id=eng_id,
        layer="engagement",
        title="Pending hypothesis",
        current_progress="proposed",
        current_confidence="medium",
        current_momentum="steady",
        confidence_inferred=True,
        momentum_inferred=True,
    )
    db_session.add(h)
    await db_session.commit()

    resp = await client.post(
        f"/v1/engagements/{eng_id}/close",
        json={
            "force": True,
            "override_reason": "Customer terminated SOW; closing with one open hypothesis pending re-scoping.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["engagement"]["ended_at"] is not None
    assert body["warnings"] == [{"open_hypotheses": 1}]


async def test_post_engagement_close_with_force_without_override_reason_returns_422(
    client: AsyncClient,
) -> None:
    """Force=True without override_reason is rejected at Pydantic validation (422)."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    resp = await client.post(
        f"/v1/engagements/{eng_id}/close",
        json={"force": True},
    )
    assert resp.status_code == 422


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
