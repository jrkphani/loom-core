"""Tests for the events API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_post_events_creates_with_generated_id_and_returns_201(
    client: AsyncClient,
) -> None:
    """POST /v1/events returns 201 with a generated ULID id and all provided fields."""
    resp = await client.post(
        "/v1/events",
        json={
            "domain": "work",
            "type": "process",
            "occurred_at": "2026-04-26T10:00:00+00:00",
            "source_path": "inbox/work/transcripts/foo.vtt",
            "source_metadata": {"attendees": ["A", "B"]},
            "body_summary": "Steerco call.",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["id"]) == 26
    assert data["type"] == "process"
    assert data["source_metadata"] == {"attendees": ["A", "B"]}
    assert data["body_summary"] == "Steerco call."
    assert data["created_at"] is not None


async def test_post_events_with_invalid_type_returns_422(client: AsyncClient) -> None:
    """Confirmation test: POST with type not in enum returns 422 from Pydantic."""
    resp = await client.post(
        "/v1/events",
        json={
            "domain": "work",
            "type": "not_a_real_type",
            "occurred_at": "2026-04-26T10:00:00+00:00",
        },
    )
    assert resp.status_code == 422


async def test_post_events_missing_occurred_at_returns_422(client: AsyncClient) -> None:
    """Confirmation test: POST without occurred_at returns 422 (required field)."""
    resp = await client.post(
        "/v1/events",
        json={"domain": "work", "type": "process"},
    )
    assert resp.status_code == 422


async def test_get_events_returns_filtered_and_ordered_list(client: AsyncClient) -> None:
    """GET /v1/events returns events ordered by occurred_at DESC with optional type filter."""
    await client.post(
        "/v1/events",
        json={"domain": "work", "type": "process", "occurred_at": "2026-04-01T10:00:00+00:00"},
    )
    await client.post(
        "/v1/events",
        json={
            "domain": "work",
            "type": "inbox_derived",
            "occurred_at": "2026-04-15T10:00:00+00:00",
        },
    )
    r3 = await client.post(
        "/v1/events",
        json={"domain": "work", "type": "process", "occurred_at": "2026-04-20T10:00:00+00:00"},
    )
    e3_id = r3.json()["id"]

    resp = await client.get("/v1/events?domain=work")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 3
    assert events[0]["id"] == e3_id

    resp2 = await client.get("/v1/events?domain=work&type=process")
    assert resp2.status_code == 200
    process_events = resp2.json()["events"]
    assert len(process_events) == 2
    assert all(e["type"] == "process" for e in process_events)
    assert process_events[0]["id"] == e3_id


async def test_get_event_by_id_returns_200_or_404(client: AsyncClient) -> None:
    """GET /v1/events/:id returns 200 for known id and 404 for unknown id."""
    post_r = await client.post(
        "/v1/events",
        json={"domain": "work", "type": "process", "occurred_at": "2026-04-26T10:00:00+00:00"},
    )
    event_id = post_r.json()["id"]

    resp = await client.get(f"/v1/events/{event_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == event_id

    not_found = await client.get("/v1/events/01HXXXXXXXXXXXXXXXXXXXXXXX")
    assert not_found.status_code == 404
    assert not_found.json()["detail"]["error"] == "NOT_FOUND"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_immutable_methods_return_405(client: AsyncClient, method: str) -> None:
    """Confirmation test: PATCH and DELETE return 405 — events are immutable by absence of handlers.

    FastAPI returns 405 automatically when a method is not registered on a path
    that has at least one other method registered (GET /events/{id} is registered).
    No explicit 405-raise handler is used; the absence of PATCH/DELETE IS the enforcement.
    """
    post_r = await client.post(
        "/v1/events",
        json={"domain": "work", "type": "process", "occurred_at": "2026-04-26T10:00:00+00:00"},
    )
    event_id = post_r.json()["id"]

    resp = await client.request(method, f"/v1/events/{event_id}")
    assert resp.status_code == 405
