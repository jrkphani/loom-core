"""Tests for the atom lifecycle status endpoints (#013).

Routes under test:
- POST /v1/atoms/{atom_id}/status
- GET  /v1/atoms/{atom_id}/status/history
- PATCH /v1/atoms/{atom_id}/commitment
- PATCH /v1/atoms/{atom_id}/risk
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.main import app
from loom_core.storage.models import (
    Atom,
    AtomAskDetails,
    AtomCommitmentDetails,
    AtomRiskDetails,
    AtomStatusChange,
    Event,
    Stakeholder,
)


async def _seed_event(session: AsyncSession) -> Event:
    event = Event(
        id=str(ULID()),
        domain="work",
        type="process",
        occurred_at=datetime.now(UTC),
        body_summary="seeded event for atom-status tests",
    )
    session.add(event)
    await session.flush()
    return event


async def _seed_stakeholder(session: AsyncSession, email: str = "alice@example.com") -> Stakeholder:
    stakeholder = Stakeholder(
        id=str(ULID()),
        canonical_name="Alice",
        primary_email=email,
    )
    session.add(stakeholder)
    await session.flush()
    return stakeholder


async def _seed_commitment_atom(
    session: AsyncSession,
    *,
    event_id: str,
    owner_stakeholder_id: str | None = None,
    initial_status: str = "open",
) -> Atom:
    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="commitment",
        event_id=event_id,
        content="Alice will deliver the SOW.",
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        extractor_provider="python_rules",
        extraction_confidence=1.0,
    )
    atom.commitment_details = AtomCommitmentDetails(
        atom_id=atom.id,
        owner_stakeholder_id=owner_stakeholder_id,
        current_status=initial_status,
    )
    session.add(atom)
    await session.flush()
    return atom


async def _seed_ask_atom(
    session: AsyncSession,
    *,
    event_id: str,
    owner_stakeholder_id: str | None = None,
    initial_status: str = "raised",
) -> Atom:
    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="ask",
        event_id=event_id,
        content="Can the AWS team confirm the budget?",
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        extractor_provider="python_rules",
        extraction_confidence=1.0,
    )
    session.add(atom)
    await session.flush()
    session.add(
        AtomAskDetails(
            atom_id=atom.id,
            owner_stakeholder_id=owner_stakeholder_id,
            current_status=initial_status,
        )
    )
    await session.flush()
    return atom


async def _seed_risk_atom(
    session: AsyncSession,
    *,
    event_id: str,
    owner_stakeholder_id: str | None = None,
    severity: str = "high",
    initial_mitigation_status: str = "unmitigated",
) -> Atom:
    atom = Atom(
        id=str(ULID()),
        domain="work",
        type="risk",
        event_id=event_id,
        content="Steerco may delay budget approval.",
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        extractor_provider="python_rules",
        extraction_confidence=1.0,
    )
    session.add(atom)
    await session.flush()
    session.add(
        AtomRiskDetails(
            atom_id=atom.id,
            severity=severity,
            owner_stakeholder_id=owner_stakeholder_id,
            mitigation_status=initial_mitigation_status,
        )
    )
    await session.flush()
    return atom


async def test_post_status_on_commitment_transitions_current_status_and_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/atoms/{id}/status on a commitment updates the detail table's
    current_status and writes a single atom_status_changes row."""
    stakeholder = await _seed_stakeholder(db_session)
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(
        db_session, event_id=event.id, owner_stakeholder_id=stakeholder.id
    )
    await db_session.commit()

    resp = await client.post(
        f"/v1/atoms/{atom.id}/status",
        json={
            "new_status": "in_progress",
            "changed_by": "phani",
            "reason": "kicking off",
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["old_status"] == "open"
    assert data["new_status"] == "in_progress"
    assert data["changed_by"] == "phani"
    assert data["reason"] == "kicking off"
    assert len(data["id"]) == 26
    assert data["changed_at"] is not None

    # DB state: verify with a fresh session. db_session has cached identity-map
    # objects from the seed phase, so reads through it return stale values
    # despite the route's commit having landed.
    async with app.state.session_factory() as verify:
        refreshed_details = (
            await verify.execute(
                select(AtomCommitmentDetails).where(AtomCommitmentDetails.atom_id == atom.id)
            )
        ).scalar_one()
        assert refreshed_details.current_status == "in_progress"
        assert refreshed_details.status_last_changed_at is not None

        audit_rows = (
            (
                await verify.execute(
                    select(AtomStatusChange).where(AtomStatusChange.atom_id == atom.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1
        audit = audit_rows[0]
        assert audit.old_status == "open"
        assert audit.new_status == "in_progress"
        assert audit.changed_by == "phani"
        assert audit.reason == "kicking off"


async def test_post_status_on_ask_transitions_current_status_and_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/atoms/{id}/status on an ask updates ask_details.current_status
    and writes an atom_status_changes row."""
    stakeholder = await _seed_stakeholder(db_session, email="aws-team@example.com")
    event = await _seed_event(db_session)
    atom = await _seed_ask_atom(db_session, event_id=event.id, owner_stakeholder_id=stakeholder.id)
    await db_session.commit()

    resp = await client.post(
        f"/v1/atoms/{atom.id}/status",
        json={"new_status": "granted", "changed_by": "phani"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["old_status"] == "raised"
    assert data["new_status"] == "granted"
    assert data["reason"] is None

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(select(AtomAskDetails).where(AtomAskDetails.atom_id == atom.id))
        ).scalar_one()
        assert details.current_status == "granted"

        audit_rows = (
            (
                await verify.execute(
                    select(AtomStatusChange).where(AtomStatusChange.atom_id == atom.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].old_status == "raised"
        assert audit_rows[0].new_status == "granted"


async def test_post_status_on_risk_transitions_mitigation_status_and_writes_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /v1/atoms/{id}/status on a risk updates `mitigation_status` (NOT
    `current_status` — column-name asymmetry per Pin 1) and writes an audit row."""
    event = await _seed_event(db_session)
    atom = await _seed_risk_atom(db_session, event_id=event.id)
    await db_session.commit()

    resp = await client.post(
        f"/v1/atoms/{atom.id}/status",
        json={"new_status": "mitigated", "changed_by": "phani"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["old_status"] == "unmitigated"
    assert data["new_status"] == "mitigated"

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(select(AtomRiskDetails).where(AtomRiskDetails.atom_id == atom.id))
        ).scalar_one()
        assert details.mitigation_status == "mitigated"

        audit_rows = (
            (
                await verify.execute(
                    select(AtomStatusChange).where(AtomStatusChange.atom_id == atom.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].old_status == "unmitigated"
        assert audit_rows[0].new_status == "mitigated"


@pytest.mark.parametrize("kind", ["decision", "status_update"])
async def test_post_status_on_non_lifecycle_kind_returns_422(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    """Non-lifecycle atom kinds (decision, status_update) cannot transition status.
    POST status returns 422 with ATOM_KIND_MISMATCH; no DB mutation."""
    event = await _seed_event(db_session)
    atom = Atom(
        id=str(ULID()),
        domain="work",
        type=kind,
        event_id=event.id,
        content=f"A {kind} atom.",
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        extractor_provider="python_rules",
        extraction_confidence=1.0,
    )
    db_session.add(atom)
    await db_session.commit()

    resp = await client.post(
        f"/v1/atoms/{atom.id}/status",
        json={"new_status": "in_progress", "changed_by": "phani"},
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ATOM_KIND_MISMATCH"
    assert kind in detail["message"]

    async with app.state.session_factory() as verify:
        audit_rows = (
            (
                await verify.execute(
                    select(AtomStatusChange).where(AtomStatusChange.atom_id == atom.id)
                )
            )
            .scalars()
            .all()
        )
        assert audit_rows == []


async def test_post_status_returns_409_on_retracted_atom(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A retracted atom rejects status transitions with 409 ATOM_RETRACTED.

    Read-path test of the existing `atoms.retracted` column (landed in #076);
    not a workaround for missing #084 mutate-side. Test seeds retracted=True
    directly via ORM assignment.
    """
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(db_session, event_id=event.id)
    atom.retracted = True
    atom.retracted_at = datetime.now(UTC)
    atom.retraction_reason = "wrong_extraction"
    await db_session.commit()

    resp = await client.post(
        f"/v1/atoms/{atom.id}/status",
        json={"new_status": "in_progress", "changed_by": "phani"},
    )

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ATOM_RETRACTED"

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(
                select(AtomCommitmentDetails).where(AtomCommitmentDetails.atom_id == atom.id)
            )
        ).scalar_one()
        assert details.current_status == "open"  # unchanged

        audit_rows = (
            (
                await verify.execute(
                    select(AtomStatusChange).where(AtomStatusChange.atom_id == atom.id)
                )
            )
            .scalars()
            .all()
        )
        assert audit_rows == []


async def test_get_history_returns_changes_ordered_by_changed_at_desc(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms/{id}/status/history returns audit rows ordered DESC by
    changed_at, with `retracted_at` null on a non-retracted atom."""
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(db_session, event_id=event.id)

    t0 = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 10, 10, 0, 0, tzinfo=UTC)
    for old, new, when in [
        ("open", "in_progress", t0),
        ("in_progress", "slipped", t1),
        ("slipped", "renegotiated", t2),
    ]:
        db_session.add(
            AtomStatusChange(
                id=str(ULID()),
                atom_id=atom.id,
                old_status=old,
                new_status=new,
                changed_at=when,
                changed_by="phani",
            )
        )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}/status/history")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["retracted_at"] is None
    changes = data["changes"]
    assert len(changes) == 3
    # DESC ordering: t2 first.
    assert changes[0]["new_status"] == "renegotiated"
    assert changes[1]["new_status"] == "slipped"
    assert changes[2]["new_status"] == "in_progress"
    # All DC8 fields populated per change.
    for c in changes:
        assert {"old_status", "new_status", "changed_at", "changed_by", "reason"} <= c.keys()


async def test_get_history_returns_retracted_at_flag_for_retracted_atom(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET history on a retracted atom returns 200 (history remains readable;
    only POST status is blocked by retraction). Response includes the
    `retracted_at` timestamp from atoms.retracted_at."""
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(db_session, event_id=event.id)

    retracted_at = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    atom.retracted = True
    atom.retracted_at = retracted_at
    atom.retraction_reason = "wrong_extraction"

    t0 = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC)
    for old, new, when in [
        ("open", "in_progress", t0),
        ("in_progress", "met", t1),
    ]:
        db_session.add(
            AtomStatusChange(
                id=str(ULID()),
                atom_id=atom.id,
                old_status=old,
                new_status=new,
                changed_at=when,
                changed_by="phani",
            )
        )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}/status/history")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["retracted_at"] is not None
    assert data["retracted_at"].startswith("2026-05-01T12:00:00")
    assert len(data["changes"]) == 2
    # DESC ordering preserved.
    assert data["changes"][0]["new_status"] == "met"
    assert data["changes"][1]["new_status"] == "in_progress"


async def test_patch_commitment_updates_due_date_and_owner_stakeholder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH /v1/atoms/{id}/commitment with both fields updates the detail row
    and returns the updated state."""
    initial_owner = await _seed_stakeholder(db_session, email="alice@example.com")
    new_owner = await _seed_stakeholder(db_session, email="bob@example.com")
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(
        db_session, event_id=event.id, owner_stakeholder_id=initial_owner.id
    )
    await db_session.commit()

    resp = await client.patch(
        f"/v1/atoms/{atom.id}/commitment",
        json={
            "due_date": "2026-06-15",
            "owner_stakeholder_id": new_owner.id,
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["due_date"] == "2026-06-15"
    assert data["owner_stakeholder_id"] == new_owner.id

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(
                select(AtomCommitmentDetails).where(AtomCommitmentDetails.atom_id == atom.id)
            )
        ).scalar_one()
        assert details.due_date == date(2026, 6, 15)
        assert details.owner_stakeholder_id == new_owner.id


async def test_patch_commitment_partial_update_only_changes_specified_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH with only `due_date` leaves owner_stakeholder_id unchanged."""
    initial_owner = await _seed_stakeholder(db_session, email="alice@example.com")
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(
        db_session, event_id=event.id, owner_stakeholder_id=initial_owner.id
    )
    await db_session.commit()

    resp = await client.patch(
        f"/v1/atoms/{atom.id}/commitment",
        json={"due_date": "2026-07-01"},
    )

    assert resp.status_code == 200, resp.text

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(
                select(AtomCommitmentDetails).where(AtomCommitmentDetails.atom_id == atom.id)
            )
        ).scalar_one()
        assert details.due_date == date(2026, 7, 1)
        assert details.owner_stakeholder_id == initial_owner.id  # unchanged


@pytest.mark.parametrize("kind", ["decision", "ask", "risk", "status_update"])
async def test_patch_commitment_on_non_commitment_kind_returns_422(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    """PATCH /commitment on any non-commitment kind returns 422 with
    ATOM_KIND_MISMATCH."""
    event = await _seed_event(db_session)
    if kind == "ask":
        atom = await _seed_ask_atom(db_session, event_id=event.id)
    elif kind == "risk":
        atom = await _seed_risk_atom(db_session, event_id=event.id)
    else:
        atom = Atom(
            id=str(ULID()),
            domain="work",
            type=kind,
            event_id=event.id,
            content=f"A {kind} atom.",
            anchor_id=f"^a-{str(ULID())[-6:].lower()}",
            extractor_provider="python_rules",
            extraction_confidence=1.0,
        )
        db_session.add(atom)
    await db_session.commit()

    resp = await client.patch(
        f"/v1/atoms/{atom.id}/commitment",
        json={"due_date": "2026-06-15"},
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ATOM_KIND_MISMATCH"


async def test_patch_commitment_with_empty_body_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH with no fields populated returns 422 (Pydantic at-least-one validator)."""
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(db_session, event_id=event.id)
    await db_session.commit()

    resp = await client.patch(f"/v1/atoms/{atom.id}/commitment", json={})

    assert resp.status_code == 422, resp.text


async def test_patch_risk_updates_severity_and_owner_stakeholder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH /v1/atoms/{id}/risk with both fields updates the detail row."""
    initial_owner = await _seed_stakeholder(db_session, email="alice@example.com")
    new_owner = await _seed_stakeholder(db_session, email="bob@example.com")
    event = await _seed_event(db_session)
    atom = await _seed_risk_atom(
        db_session,
        event_id=event.id,
        owner_stakeholder_id=initial_owner.id,
        severity="medium",
    )
    await db_session.commit()

    resp = await client.patch(
        f"/v1/atoms/{atom.id}/risk",
        json={"severity": "high", "owner_stakeholder_id": new_owner.id},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["severity"] == "high"
    assert data["owner_stakeholder_id"] == new_owner.id

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(select(AtomRiskDetails).where(AtomRiskDetails.atom_id == atom.id))
        ).scalar_one()
        assert details.severity == "high"
        assert details.owner_stakeholder_id == new_owner.id


async def test_patch_risk_partial_update_only_changes_specified_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH with only `severity` leaves owner_stakeholder_id unchanged."""
    initial_owner = await _seed_stakeholder(db_session, email="alice@example.com")
    event = await _seed_event(db_session)
    atom = await _seed_risk_atom(
        db_session,
        event_id=event.id,
        owner_stakeholder_id=initial_owner.id,
        severity="medium",
    )
    await db_session.commit()

    resp = await client.patch(
        f"/v1/atoms/{atom.id}/risk",
        json={"severity": "critical"},
    )

    assert resp.status_code == 200, resp.text

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(select(AtomRiskDetails).where(AtomRiskDetails.atom_id == atom.id))
        ).scalar_one()
        assert details.severity == "critical"
        assert details.owner_stakeholder_id == initial_owner.id


@pytest.mark.parametrize("kind", ["decision", "commitment", "ask", "status_update"])
async def test_patch_risk_on_non_risk_kind_returns_422(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    """PATCH /risk on any non-risk kind returns 422 ATOM_KIND_MISMATCH."""
    event = await _seed_event(db_session)
    if kind == "commitment":
        atom = await _seed_commitment_atom(db_session, event_id=event.id)
    elif kind == "ask":
        atom = await _seed_ask_atom(db_session, event_id=event.id)
    else:
        atom = Atom(
            id=str(ULID()),
            domain="work",
            type=kind,
            event_id=event.id,
            content=f"A {kind} atom.",
            anchor_id=f"^a-{str(ULID())[-6:].lower()}",
            extractor_provider="python_rules",
            extraction_confidence=1.0,
        )
        db_session.add(atom)
    await db_session.commit()

    resp = await client.patch(
        f"/v1/atoms/{atom.id}/risk",
        json={"severity": "high"},
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ATOM_KIND_MISMATCH"


async def test_patch_risk_with_empty_body_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH /risk with no fields populated returns 422."""
    event = await _seed_event(db_session)
    atom = await _seed_risk_atom(db_session, event_id=event.id)
    await db_session.commit()

    resp = await client.patch(f"/v1/atoms/{atom.id}/risk", json={})

    assert resp.status_code == 422, resp.text


async def test_post_status_with_invalid_status_for_kind_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A status string outside the kind's CHECK enum returns 422 with
    ATOM_STATUS_INVALID; no DB mutation."""
    event = await _seed_event(db_session)
    atom = await _seed_commitment_atom(db_session, event_id=event.id)
    await db_session.commit()

    # 'granted' is an ask state, not a commitment state.
    resp = await client.post(
        f"/v1/atoms/{atom.id}/status",
        json={"new_status": "granted", "changed_by": "phani"},
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "ATOM_STATUS_INVALID"
    assert "granted" in detail["message"]

    async with app.state.session_factory() as verify:
        details = (
            await verify.execute(
                select(AtomCommitmentDetails).where(AtomCommitmentDetails.atom_id == atom.id)
            )
        ).scalar_one()
        assert details.current_status == "open"  # unchanged

        audit_rows = (
            (
                await verify.execute(
                    select(AtomStatusChange).where(AtomStatusChange.atom_id == atom.id)
                )
            )
            .scalars()
            .all()
        )
        assert audit_rows == []
