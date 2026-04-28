"""Tests for the hypotheses API endpoints."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.storage.models import Hypothesis, TriageItem


async def test_post_hypotheses_creates_arena_level(client: AsyncClient) -> None:
    """POST /v1/hypotheses creates an arena-level hypothesis with engagement_id=null."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Panasonic"})
    arena_id = arena_r.json()["id"]

    resp = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": None,
            "layer": "arena",
            "title": "Panasonic relationship deepens to multi-wave program by FY27",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["layer"] == "arena"
    assert data["engagement_id"] is None
    assert data["current_progress"] == "proposed"
    assert data["current_confidence"] == "medium"
    assert data["current_momentum"] == "steady"
    assert data["confidence_inferred"] is True
    assert data["momentum_inferred"] is True


async def test_post_hypotheses_engagement_layer_requires_engagement_id(
    client: AsyncClient,
) -> None:
    """POST with layer=engagement and no engagement_id returns 422."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]

    resp = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": None,
            "layer": "engagement",
            "title": "Test hypothesis",
        },
    )
    assert resp.status_code == 422


async def test_post_hypotheses_arena_layer_forbids_engagement_id(
    client: AsyncClient,
) -> None:
    """POST with layer=arena and an engagement_id returns 422."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    resp = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "arena",
            "title": "Test hypothesis",
        },
    )
    assert resp.status_code == 422


async def test_post_hypotheses_with_invalid_arena_returns_404(client: AsyncClient) -> None:
    """POST with non-existent arena_id returns 404."""
    resp = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "engagement_id": None,
            "layer": "arena",
            "title": "Test",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NOT_FOUND"


async def test_post_hypotheses_with_invalid_engagement_returns_404(
    client: AsyncClient,
) -> None:
    """POST with non-existent engagement_id returns 404."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]

    resp = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": "01HXXXXXXXXXXXXXXXXXXXXXXX",
            "layer": "engagement",
            "title": "Test",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NOT_FOUND"


async def test_get_hypothesis_by_id_returns_full_state(client: AsyncClient) -> None:
    """GET /v1/hypotheses/:id returns 200 with all fields; bogus ID returns 404."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Test hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    resp = await client.get(f"/v1/hypotheses/{hyp_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == hyp_id
    assert data["layer"] == "engagement"
    assert data["current_progress"] == "proposed"
    assert data["current_confidence"] == "medium"
    assert data["current_momentum"] == "steady"
    assert data["confidence_inferred"] is True
    assert data["momentum_inferred"] is True
    assert data["closed_at"] is None

    not_found = await client.get("/v1/hypotheses/01HXXXXXXXXXXXXXXXXXXXXXXX")
    assert not_found.status_code == 404
    assert not_found.json()["detail"]["error"] == "NOT_FOUND"


async def test_list_hypotheses_with_filters(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /v1/hypotheses filters by engagement_id, arena_id+layer; requires a filter."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from loom_core.storage.models import Hypothesis

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]

    # Two engagement-level hypotheses.
    e1_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "E1",
        },
    )
    e1_id = e1_r.json()["id"]
    e2_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "E2",
        },
    )
    e2_id = e2_r.json()["id"]

    # Guarantee ordering by giving E1 an older timestamp via DB.
    now = datetime.now(UTC)
    await db_session.execute(
        update(Hypothesis)
        .where(Hypothesis.id == e1_id)
        .values(created_at=now - timedelta(seconds=5))
    )
    await db_session.commit()

    # One arena-level hypothesis.
    a1_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": None,
            "layer": "arena",
            "title": "A1",
        },
    )
    a1_id = a1_r.json()["id"]

    # Filter by engagement_id → E1 and E2 only, ordered DESC (E2 first).
    resp = await client.get(f"/v1/hypotheses?engagement_id={eng_id}")
    assert resp.status_code == 200
    hyps = resp.json()["hypotheses"]
    ids = [h["id"] for h in hyps]
    assert len(ids) == 2
    assert ids[0] == e2_id  # newer
    assert ids[1] == e1_id  # older

    # Filter by arena_id+layer=arena → A1 only.
    resp2 = await client.get(f"/v1/hypotheses?arena_id={arena_id}&layer=arena")
    assert resp2.status_code == 200
    hyps2 = resp2.json()["hypotheses"]
    assert len(hyps2) == 1
    assert hyps2[0]["id"] == a1_id

    # No filters → 422.
    resp3 = await client.get("/v1/hypotheses")
    assert resp3.status_code == 422


async def test_post_hypotheses_creates_engagement_level(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/hypotheses returns 201 with full state fields for engagement layer."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Panasonic"})
    assert arena_r.status_code == 201
    arena_id = arena_r.json()["id"]

    eng_r = await client.post(
        "/v1/engagements",
        json={"domain": "work", "arena_id": arena_id, "name": "Wave 2"},
    )
    assert eng_r.status_code == 201
    eng_id = eng_r.json()["id"]

    resp = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Wave 2 SAP cutover completes by Sept 30 with zero data-loss",
        },
    )
    assert resp.status_code == 201
    data = resp.json()

    assert len(data["id"]) == 26
    assert data["layer"] == "engagement"
    assert data["arena_id"] == arena_id
    assert data["engagement_id"] == eng_id
    assert data["title"] == "Wave 2 SAP cutover completes by Sept 30 with zero data-loss"
    assert data["description"] is None
    assert data["current_progress"] == "proposed"
    assert data["current_confidence"] == "medium"
    assert data["current_momentum"] == "steady"
    assert data["confidence_inferred"] is True
    assert data["momentum_inferred"] is True
    assert data["progress_last_changed_at"] is None
    assert data["confidence_last_reviewed_at"] is None
    assert data["momentum_last_reviewed_at"] is None
    assert data["closed_at"] is None
    assert "created_at" in data

    # Verify row persisted.
    result = await db_session.execute(select(Hypothesis).where(Hypothesis.id == data["id"]))
    row = result.scalar_one()
    assert row.layer == "engagement"
    assert row.engagement_id == eng_id


async def test_post_hypothesis_close_with_terminal_state_returns_200(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/hypotheses/:id/close with terminal progress returns 200 + closed_at."""
    from sqlalchemy import update

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Will close",
        },
    )
    hyp_id = post_r.json()["id"]

    # Set terminal state directly via DB (state-change API is #005).
    await db_session.execute(
        update(Hypothesis).where(Hypothesis.id == hyp_id).values(current_progress="realised")
    )
    await db_session.commit()

    close_r = await client.post(f"/v1/hypotheses/{hyp_id}/close")
    assert close_r.status_code == 200
    assert close_r.json()["closed_at"] is not None

    get_r = await client.get(f"/v1/hypotheses/{hyp_id}")
    assert get_r.json()["closed_at"] is not None


async def test_post_hypothesis_close_with_non_terminal_returns_422(
    client: AsyncClient,
) -> None:
    """POST /v1/hypotheses/:id/close with non-terminal progress returns 422."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Will not close",
        },
    )
    hyp_id = post_r.json()["id"]  # current_progress defaults to "proposed"

    close_r = await client.post(f"/v1/hypotheses/{hyp_id}/close")
    assert close_r.status_code == 422
    detail = close_r.json()["detail"]
    assert detail["error"] == "VALIDATION"
    assert "proposed" in detail["message"]


async def test_post_hypothesis_close_on_already_closed_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/hypotheses/:id/close on an already-closed hypothesis returns 409."""
    from sqlalchemy import update

    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Will be closed twice",
        },
    )
    hyp_id = post_r.json()["id"]

    # Set terminal state and close once.
    await db_session.execute(
        update(Hypothesis).where(Hypothesis.id == hyp_id).values(current_progress="confirmed")
    )
    await db_session.commit()

    first = await client.post(f"/v1/hypotheses/{hyp_id}/close")
    assert first.status_code == 200

    second = await client.post(f"/v1/hypotheses/{hyp_id}/close")
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "CONFLICT"


async def test_patch_hypothesis_updates_title_and_description(
    client: AsyncClient,
) -> None:
    """PATCH /v1/hypotheses/:id updates title and description; state fields rejected."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "T1",
        },
    )
    hyp_id = post_r.json()["id"]

    patch_r = await client.patch(
        f"/v1/hypotheses/{hyp_id}",
        json={"title": "T1 Renamed", "description": "added context"},
    )
    assert patch_r.status_code == 200
    assert patch_r.json()["title"] == "T1 Renamed"
    assert patch_r.json()["description"] == "added context"

    get_r = await client.get(f"/v1/hypotheses/{hyp_id}")
    assert get_r.json()["title"] == "T1 Renamed"

    # State fields are forbidden via extra="forbid".
    bad_r = await client.patch(
        f"/v1/hypotheses/{hyp_id}",
        json={"current_progress": "in_delivery"},
    )
    assert bad_r.status_code == 422


async def test_get_hypothesis_state_returns_default_state_for_fresh_hypothesis(
    client: AsyncClient,
) -> None:
    """GET /v1/hypotheses/:id/state returns 200 with default 3-d state for a fresh hypothesis."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Fresh hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    resp = await client.get(f"/v1/hypotheses/{hyp_id}/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["progress"] == "proposed"
    assert data["confidence"] == "medium"
    assert data["momentum"] == "steady"
    assert data["progress_last_changed_at"] is None
    assert data["confidence_last_reviewed_at"] is None
    assert data["momentum_last_reviewed_at"] is None
    assert data["confidence_inferred"] is True
    assert data["momentum_inferred"] is True
    assert "progress_inferred" not in data


async def test_get_hypothesis_state_returns_404_for_unknown_id(
    client: AsyncClient,
) -> None:
    """GET /v1/hypotheses/:id/state returns 404 with error=NOT_FOUND for a bogus ID."""
    resp = await client.get("/v1/hypotheses/01HXXXXXXXXXXXXXXXXXXXXXXX/state")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NOT_FOUND"


async def test_get_hypothesis_state_history_empty_when_no_changes(
    client: AsyncClient,
) -> None:
    """GET /v1/hypotheses/:id/state/history returns 200 with empty list when no changes exist."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Fresh hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    resp = await client.get(f"/v1/hypotheses/{hyp_id}/state/history")
    assert resp.status_code == 200
    assert resp.json() == {"history": []}


async def test_get_hypothesis_state_history_returns_404_for_unknown_id(
    client: AsyncClient,
) -> None:
    """GET /v1/hypotheses/:id/state/history returns 404 for a bogus hypothesis ID."""
    resp = await client.get("/v1/hypotheses/01HXXXXXXXXXXXXXXXXXXXXXXX/state/history")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NOT_FOUND"


async def test_get_hypothesis_state_proposals_empty_when_no_triage_items(
    client: AsyncClient,
) -> None:
    """GET /v1/hypotheses/:id/state/proposals returns 200 with empty list when no items exist."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Fresh hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    resp = await client.get(f"/v1/hypotheses/{hyp_id}/state/proposals")
    assert resp.status_code == 200
    assert resp.json() == {"proposals": []}


async def test_get_hypothesis_state_proposals_returns_404_for_unknown_id(
    client: AsyncClient,
) -> None:
    """GET /v1/hypotheses/:id/state/proposals returns 404 for a bogus hypothesis ID."""
    resp = await client.get("/v1/hypotheses/01HXXXXXXXXXXXXXXXXXXXXXXX/state/proposals")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NOT_FOUND"


async def test_post_confirm_state_proposal_returns_200_and_persists_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /confirm resolves proposal, writes audit row, updates denormalized state."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Test hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    # Insert a pending proposal directly via db_session.
    proposal = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_id,
        resolved_at=None,
    )
    db_session.add(proposal)
    await db_session.commit()

    resp = await client.post(
        f"/v1/hypotheses/{hyp_id}/state/proposals/{proposal.id}/confirm",
        json={"dimension": "confidence", "new_value": "high"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "state_change_id" in data
    assert data["hypothesis_id"] == hyp_id
    assert data["dimension"] == "confidence"
    assert data["old_value"] == "medium"
    assert data["new_value"] == "high"
    assert data["changed_by"] == "human_confirmed"
    assert data["override_reason"] is None
    assert data["supporting_atoms"] == []
    assert data["proposal_resolved"] is True

    # GET /state reflects the new confidence.
    state_r = await client.get(f"/v1/hypotheses/{hyp_id}/state")
    assert state_r.json()["confidence"] == "high"
    assert state_r.json()["confidence_inferred"] is False

    # GET /state/history contains the new change.
    hist_r = await client.get(f"/v1/hypotheses/{hyp_id}/state/history")
    history = hist_r.json()["history"]
    assert len(history) == 1
    assert history[0]["changed_by"] == "human_confirmed"

    # GET /state/proposals shows empty (proposal resolved).
    props_r = await client.get(f"/v1/hypotheses/{hyp_id}/state/proposals")
    assert props_r.json()["proposals"] == []


async def test_post_override_state_proposal_returns_200_and_persists_reason(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /override resolves proposal with changed_by=human_overridden and stores reason."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Test hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    proposal = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_id,
        resolved_at=None,
    )
    db_session.add(proposal)
    await db_session.commit()

    reason = "The slipped commitments are admin friction, not alignment risk."
    resp = await client.post(
        f"/v1/hypotheses/{hyp_id}/state/proposals/{proposal.id}/override",
        json={"dimension": "confidence", "new_value": "high", "override_reason": reason},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["changed_by"] == "human_overridden"
    assert data["override_reason"] == reason
    assert data["proposal_resolved"] is True

    # GET /state/history shows the override row most recently.
    hist_r = await client.get(f"/v1/hypotheses/{hyp_id}/state/history")
    history = hist_r.json()["history"]
    assert len(history) == 1
    assert history[0]["changed_by"] == "human_overridden"
    assert history[0]["override_reason"] == reason


async def test_post_override_returns_422_when_override_reason_missing(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /override without override_reason returns 422 from Pydantic validation."""
    arena_r = await client.post("/v1/arenas", json={"domain": "work", "name": "Corp"})
    arena_id = arena_r.json()["id"]
    eng_r = await client.post(
        "/v1/engagements", json={"domain": "work", "arena_id": arena_id, "name": "Wave 1"}
    )
    eng_id = eng_r.json()["id"]
    post_r = await client.post(
        "/v1/hypotheses",
        json={
            "domain": "work",
            "arena_id": arena_id,
            "engagement_id": eng_id,
            "layer": "engagement",
            "title": "Test hypothesis",
        },
    )
    hyp_id = post_r.json()["id"]

    proposal = TriageItem(
        id=str(ULID()),
        item_type="state_change_proposal",
        related_entity_type="hypothesis",
        related_entity_id=hyp_id,
        resolved_at=None,
    )
    db_session.add(proposal)
    await db_session.commit()

    resp = await client.post(
        f"/v1/hypotheses/{hyp_id}/state/proposals/{proposal.id}/override",
        json={"dimension": "confidence", "new_value": "high"},
    )
    assert resp.status_code == 422
