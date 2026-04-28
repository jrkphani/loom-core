"""Tests for the external references API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Atom, Event


async def test_post_external_references_returns_201_with_generated_id(
    client: AsyncClient,
) -> None:
    """POST /v1/external-references creates a ref and returns 201 with a generated id."""
    resp = await client.post(
        "/v1/external-references",
        json={
            "ref_type": "url",
            "ref_value": "https://example.com",
            "summary_md_path": "outbox/work/external/foo.md",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["id"]) == 26
    assert data["ref_type"] == "url"
    assert data["ref_value"] == "https://example.com"
    assert data["unreachable"] is False
    assert data["captured_at"] is not None


async def test_post_external_references_duplicate_returns_200_with_same_id(
    client: AsyncClient,
) -> None:
    """POST with same (ref_type, ref_value) twice returns 200 on second call with same id."""
    r1 = await client.post(
        "/v1/external-references",
        json={"ref_type": "url", "ref_value": "https://example.com"},
    )
    assert r1.status_code == 201
    id1 = r1.json()["id"]

    r2 = await client.post(
        "/v1/external-references",
        json={"ref_type": "url", "ref_value": "https://example.com"},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == id1


async def test_get_external_reference_by_id_returns_200_or_404(
    client: AsyncClient,
) -> None:
    """GET /v1/external-references/:id returns 200 for known id and 404 for unknown."""
    post_r = await client.post(
        "/v1/external-references",
        json={"ref_type": "url", "ref_value": "https://example.com"},
    )
    ref_id = post_r.json()["id"]

    resp = await client.get(f"/v1/external-references/{ref_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == ref_id

    not_found = await client.get("/v1/external-references/01HXXXXXXXXXXXXXXXXXXXXXXX")
    assert not_found.status_code == 404
    assert not_found.json()["detail"]["error"] == "NOT_FOUND"


async def test_post_atom_external_refs_returns_201_when_link_created(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /v1/atoms/:id/external-refs creates a link and returns 201."""
    ev = Event(
        id=str(ULID()),
        domain="work",
        type="process",
        occurred_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
    )
    db_session.add(ev)
    await db_session.flush()

    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="decision",
        event_id=ev.id,
        content="Test",
        anchor_id="d-001",
    )
    db_session.add(atom)
    await db_session.flush()
    await db_session.commit()

    ref_r = await client.post(
        "/v1/external-references",
        json={"ref_type": "url", "ref_value": "https://example.com"},
    )
    ref_id = ref_r.json()["id"]

    resp = await client.post(
        f"/v1/atoms/{atom.id}/external-refs",
        json={"external_ref_id": ref_id},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["external_ref_id"] == ref_id


@pytest.mark.parametrize("scenario", ["missing_atom", "with_link"])
async def test_get_atom_external_refs_returns_linked_refs_or_404(
    client: AsyncClient,
    db_session: AsyncSession,
    scenario: str,
) -> None:
    """GET /v1/atoms/:id/external-refs returns 404 for missing atom, or list of linked refs."""
    if scenario == "missing_atom":
        resp = await client.get(f"/v1/atoms/{ULID()!s}/external-refs")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "NOT_FOUND"
    else:
        ev = Event(
            id=str(ULID()),
            domain="work",
            type="process",
            occurred_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
        )
        db_session.add(ev)
        await db_session.flush()
        atom = Atom(
            id=str(ULID()),
            domain="work",
            type="decision",
            event_id=ev.id,
            content="Test",
            anchor_id="d-001",
        )
        db_session.add(atom)
        await db_session.flush()
        await db_session.commit()

        ref_r = await client.post(
            "/v1/external-references",
            json={"ref_type": "url", "ref_value": "https://example.com/b11"},
        )
        ref_id = ref_r.json()["id"]

        await client.post(
            f"/v1/atoms/{atom.id}/external-refs",
            json={"external_ref_id": ref_id},
        )

        resp = await client.get(f"/v1/atoms/{atom.id}/external-refs")
        assert resp.status_code == 200
        refs = resp.json()["external_references"]
        assert len(refs) == 1
        assert refs[0]["id"] == ref_id
