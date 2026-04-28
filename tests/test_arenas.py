"""Tests for the arenas API endpoints."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loom_core.storage.models import Arena


async def test_get_arena_by_id_returns_arena(client: AsyncClient) -> None:
    """GET /v1/arenas/:id returns 200 with arena fields and null work_metadata."""
    create = await client.post(
        "/v1/arenas",
        json={"domain": "work", "name": "Acme", "description": "A customer"},
    )
    assert create.status_code == 201
    arena_id = create.json()["id"]

    response = await client.get(f"/v1/arenas/{arena_id}")
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == arena_id
    assert data["domain"] == "work"
    assert data["name"] == "Acme"
    assert data["description"] == "A customer"
    assert data["closed_at"] is None
    assert "created_at" in data
    # No metadata row yet → field present but null.
    assert "work_metadata" in data
    assert data["work_metadata"] is None


async def test_get_arena_by_id_not_found_returns_404(client: AsyncClient) -> None:
    """GET /v1/arenas/:id with unknown id returns 404 with error envelope."""
    response = await client.get("/v1/arenas/01HXXXXXXXXXXXXXXXXXXXXXXX")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"] == "NOT_FOUND"


async def test_get_arena_returns_work_metadata_when_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/arenas/:id returns populated work_metadata when a row exists."""
    from loom_core.storage.models import WorkAccountMetadata

    create = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    assert create.status_code == 201
    arena_id = create.json()["id"]

    # Insert metadata directly via the DB session.
    meta = WorkAccountMetadata(
        arena_id=arena_id,
        industry="Manufacturing",
        region="JP",
        aws_segment="ENT",
        customer_type="enterprise",
    )
    db_session.add(meta)
    await db_session.commit()

    response = await client.get(f"/v1/arenas/{arena_id}")
    assert response.status_code == 200

    wm = response.json()["work_metadata"]
    assert wm is not None
    assert wm["industry"] == "Manufacturing"
    assert wm["region"] == "JP"
    assert wm["aws_segment"] == "ENT"
    assert wm["customer_type"] == "enterprise"


async def test_patch_arena_updates_name_and_description(client: AsyncClient) -> None:
    """PATCH /v1/arenas/:id updates name and description; persists."""
    create = await client.post("/v1/arenas", json={"domain": "work", "name": "Foo"})
    assert create.status_code == 201
    arena_id = create.json()["id"]

    patch_resp = await client.patch(
        f"/v1/arenas/{arena_id}",
        json={"name": "Foo Renamed", "description": "updated"},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["name"] == "Foo Renamed"
    assert data["description"] == "updated"

    get_resp = await client.get(f"/v1/arenas/{arena_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Foo Renamed"
    assert get_resp.json()["description"] == "updated"


async def test_patch_arena_updates_work_metadata(client: AsyncClient) -> None:
    """PATCH /v1/arenas/:id upserts work_metadata; partial second patch preserves fields."""
    create = await client.post("/v1/arenas", json={"domain": "work", "name": "Meta Corp"})
    assert create.status_code == 201
    arena_id = create.json()["id"]

    # First patch — create the metadata row.
    r1 = await client.patch(
        f"/v1/arenas/{arena_id}",
        json={
            "work_metadata": {
                "industry": "Manufacturing",
                "region": "JP",
                "aws_segment": "ENT",
                "customer_type": "enterprise",
            }
        },
    )
    assert r1.status_code == 200
    wm = r1.json()["work_metadata"]
    assert wm["industry"] == "Manufacturing"
    assert wm["region"] == "JP"

    # Partial second patch — only region changes; industry should survive.
    r2 = await client.patch(
        f"/v1/arenas/{arena_id}",
        json={"work_metadata": {"region": "SG"}},
    )
    assert r2.status_code == 200
    wm2 = r2.json()["work_metadata"]
    assert wm2["region"] == "SG"
    assert wm2["industry"] == "Manufacturing"  # preserved

    # Confirm persistence.
    get_r = await client.get(f"/v1/arenas/{arena_id}")
    assert get_r.json()["work_metadata"]["region"] == "SG"
    assert get_r.json()["work_metadata"]["industry"] == "Manufacturing"


async def test_post_arena_close_sets_closed_at(client: AsyncClient) -> None:
    """POST /v1/arenas/:id/close sets closed_at to a recent datetime."""
    from datetime import UTC, datetime

    create = await client.post("/v1/arenas", json={"domain": "work", "name": "ToClose"})
    assert create.status_code == 201
    arena_id = create.json()["id"]
    assert create.json()["closed_at"] is None

    before = datetime.now(UTC)
    close_resp = await client.post(f"/v1/arenas/{arena_id}/close")
    assert close_resp.status_code == 200

    closed_at_str = close_resp.json()["closed_at"]
    assert closed_at_str is not None
    closed_at = datetime.fromisoformat(closed_at_str)
    assert closed_at >= before.replace(tzinfo=None)  # DB stores naive UTC

    get_resp = await client.get(f"/v1/arenas/{arena_id}")
    assert get_resp.json()["closed_at"] is not None


async def test_post_arena_close_on_closed_arena_returns_409(client: AsyncClient) -> None:
    """POST /v1/arenas/:id/close on an already-closed arena returns 409."""
    create = await client.post("/v1/arenas", json={"domain": "work", "name": "AlreadyClosed"})
    assert create.status_code == 201
    arena_id = create.json()["id"]

    first = await client.post(f"/v1/arenas/{arena_id}/close")
    assert first.status_code == 200

    second = await client.post(f"/v1/arenas/{arena_id}/close")
    assert second.status_code == 409
    body = second.json()
    assert body["detail"]["error"] == "CONFLICT"


async def test_list_arenas_excludes_closed_when_include_closed_false(
    client: AsyncClient,
) -> None:
    """GET /v1/arenas with include_closed=false omits closed arenas; default is false."""
    # Create two arenas.
    r1 = await client.post("/v1/arenas", json={"domain": "work", "name": "Open Arena"})
    assert r1.status_code == 201
    open_id = r1.json()["id"]

    r2 = await client.post("/v1/arenas", json={"domain": "work", "name": "Closed Arena"})
    assert r2.status_code == 201
    closed_id = r2.json()["id"]
    await client.post(f"/v1/arenas/{closed_id}/close")

    # include_closed=false (explicit) → only the open arena.
    resp = await client.get("/v1/arenas?domain=work&include_closed=false")
    assert resp.status_code == 200
    ids = {a["id"] for a in resp.json()["arenas"]}
    assert open_id in ids
    assert closed_id not in ids

    # include_closed=true → both arenas.
    resp2 = await client.get("/v1/arenas?domain=work&include_closed=true")
    ids2 = {a["id"] for a in resp2.json()["arenas"]}
    assert open_id in ids2
    assert closed_id in ids2

    # No include_closed param → default false → only open.
    resp3 = await client.get("/v1/arenas?domain=work")
    ids3 = {a["id"] for a in resp3.json()["arenas"]}
    assert open_id in ids3
    assert closed_id not in ids3


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
