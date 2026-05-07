"""Tests for atom search and provenance read endpoints (#016).

Routes under test:
- GET /v1/atoms                          — list with filters (B1-B4)
- GET /v1/atoms/{atom_id}                — atom + nested source + kind details (B5-B8)
- GET /v1/atoms/{atom_id}/provenance     — atom + source + external refs (B9-B10)

Mirrors test_atoms_status.py conventions: top-level layout, async client +
db_session fixtures from conftest, inline minimal-Stakeholder seeding,
fresh verification session via app.state.session_factory() when reading
DB state after a route mutation (rare here — #016 is mostly read-only).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from loom_core.api._deps import get_audience
from loom_core.main import app
from loom_core.storage.models import (
    Arena,
    Atom,
    AtomAttachment,
    AtomCommitmentDetails,
    AtomExternalRef,
    AtomRiskDetails,
    Domain,
    Engagement,
    Event,
    ExternalReference,
    Hypothesis,
    Stakeholder,
)
from loom_core.storage.visibility import Audience


async def _seed_event(
    session: AsyncSession,
    *,
    event_type: str = "process",
    occurred_at: datetime | None = None,
    source_path: str | None = None,
    body_summary: str | None = None,
) -> Event:
    event = Event(
        id=str(ULID()),
        domain="work",
        type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        source_path=source_path,
        body_summary=body_summary,
    )
    session.add(event)
    await session.flush()
    return event


async def _seed_atom(
    session: AsyncSession,
    *,
    domain: str = "work",
    atom_type: str = "decision",
    event_id: str | None = None,
    artifact_id: str | None = None,
    content: str = "Some decision content.",
    confidence_sort_key: float = 0.5,
    dismissed: bool = False,
    created_at: datetime | None = None,
    visibility_scope: str = "private",
) -> Atom:
    atom = Atom(
        id=str(ULID()),
        domain=domain,
        type=atom_type,
        event_id=event_id,
        artifact_id=artifact_id,
        content=content,
        anchor_id=f"^a-{str(ULID())[-6:].lower()}",
        confidence_sort_key=confidence_sort_key,
        dismissed=dismissed,
        visibility_scope=visibility_scope,
        extractor_provider="python_rules",
        extraction_confidence=1.0,
    )
    if created_at is not None:
        atom.created_at = created_at
    session.add(atom)
    await session.flush()
    return atom


@pytest.mark.parametrize(
    "filter_param, filter_value, expected_kind",
    [
        ("domain", "work", "by_domain"),
        ("type", "commitment", "by_type"),
        ("event_id", None, "by_event_id"),  # value resolved per-test
        ("dismissed", "true", "by_dismissed"),
    ],
)
async def test_list_atoms_filters_by_column(
    client: AsyncClient,
    db_session: AsyncSession,
    filter_param: str,
    filter_value: str | None,
    expected_kind: str,
) -> None:
    """GET /v1/atoms applies single-column filters (DC1, DC10).

    Seeds 6 atoms across 2 domains x 3 types, mix of dismissed states. One
    atom is bound to a target event_id. Each parametrized case asserts the
    filter narrows results correctly.
    """
    # Seed: events for source attribution.
    event_a = await _seed_event(db_session)
    event_b = await _seed_event(db_session)  # The "target" for event_id filter.

    # 6 atoms: 2 domains x 3 types, plus one extra to vary dismissed state.
    # We're in single-domain v1, but DC10 says filters AND together — verify
    # domain filter still narrows. Use 'work' and a synthetic 'finance' domain.
    db_session.add(Domain(id="finance", display_name="Finance", privacy_tier="standard"))
    await db_session.flush()

    seeds: list[Atom] = []
    seeds.append(
        await _seed_atom(db_session, domain="work", atom_type="decision", event_id=event_a.id)
    )
    seeds.append(
        await _seed_atom(db_session, domain="work", atom_type="commitment", event_id=event_a.id)
    )
    seeds.append(await _seed_atom(db_session, domain="work", atom_type="risk", event_id=event_a.id))
    seeds.append(
        await _seed_atom(db_session, domain="finance", atom_type="decision", event_id=event_a.id)
    )
    target_event_atom = await _seed_atom(
        db_session, domain="work", atom_type="commitment", event_id=event_b.id
    )
    seeds.append(target_event_atom)
    dismissed_atom = await _seed_atom(
        db_session, domain="work", atom_type="decision", event_id=event_a.id, dismissed=True
    )
    seeds.append(dismissed_atom)
    await db_session.commit()

    # Resolve param value for event_id case.
    if filter_param == "event_id":
        filter_value = event_b.id

    resp = await client.get(f"/v1/atoms?{filter_param}={filter_value}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "atoms" in data
    returned_ids = {a["id"] for a in data["atoms"]}

    if expected_kind == "by_domain":
        # Default dismissed=false also applies; expect 3 work atoms (decision,
        # commitment, risk; the second commitment from event_b is also work)
        # minus the dismissed one. So: decision-a, commitment-a, risk, commitment-b = 4.
        assert all(a["domain"] == "work" for a in data["atoms"])
        # The dismissed one is excluded by default.
        assert dismissed_atom.id not in returned_ids
        assert len(data["atoms"]) == 4
    elif expected_kind == "by_type":
        assert all(a["type"] == "commitment" for a in data["atoms"])
        # Two commitment atoms seeded, neither dismissed.
        assert len(data["atoms"]) == 2
    elif expected_kind == "by_event_id":
        assert all(a["event_id"] == event_b.id for a in data["atoms"])
        assert len(data["atoms"]) == 1
        assert data["atoms"][0]["id"] == target_event_atom.id
    elif expected_kind == "by_dismissed":
        # ?dismissed=true returns only dismissed atoms (DC1).
        assert all(a["dismissed"] is True for a in data["atoms"])
        assert len(data["atoms"]) == 1
        assert data["atoms"][0]["id"] == dismissed_atom.id


async def test_list_atoms_default_ordering_and_dismissed_excluded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms with no filters returns non-dismissed atoms ordered by
    `confidence_sort_key DESC, created_at DESC` (DC8) with default
    `dismissed=False` (DC1)."""
    event = await _seed_event(db_session)

    base_time = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)

    # Two atoms share confidence 0.7 to exercise the secondary created_at sort.
    high_conf = await _seed_atom(
        db_session,
        event_id=event.id,
        confidence_sort_key=0.9,
        created_at=base_time + timedelta(days=1),
    )
    mid_conf_late = await _seed_atom(
        db_session,
        event_id=event.id,
        confidence_sort_key=0.7,
        created_at=base_time + timedelta(days=3),
    )
    mid_conf_early = await _seed_atom(
        db_session,
        event_id=event.id,
        confidence_sort_key=0.7,
        created_at=base_time + timedelta(days=2),
    )
    low_conf_dismissed = await _seed_atom(
        db_session,
        event_id=event.id,
        confidence_sort_key=0.3,
        created_at=base_time + timedelta(days=4),
        dismissed=True,
    )
    high_conf_dismissed = await _seed_atom(
        db_session,
        event_id=event.id,
        confidence_sort_key=0.95,
        created_at=base_time + timedelta(days=5),
        dismissed=True,
    )
    await db_session.commit()

    resp = await client.get("/v1/atoms")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    returned_ids = [a["id"] for a in data["atoms"]]
    # Default dismissed=False excludes the two dismissed atoms.
    assert low_conf_dismissed.id not in returned_ids
    assert high_conf_dismissed.id not in returned_ids
    # Three non-dismissed atoms remain, ordered by confidence DESC, then
    # created_at DESC for ties.
    assert returned_ids == [high_conf.id, mid_conf_late.id, mid_conf_early.id]


async def test_list_atoms_filters_by_hypothesis_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms?hypothesis_id=X returns only atoms attached to X via
    `atom_attachments` (DC9). Unattached atoms are excluded."""
    # Seed arena → engagement → hypothesis to support an atom_attachment row.
    arena = Arena(id=str(ULID()), domain="work", name="Test arena")
    db_session.add(arena)
    await db_session.flush()
    engagement = Engagement(id=str(ULID()), domain="work", arena_id=arena.id, name="Wave")
    db_session.add(engagement)
    await db_session.flush()
    hypothesis = Hypothesis(
        id=str(ULID()),
        domain="work",
        arena_id=arena.id,
        engagement_id=engagement.id,
        layer="engagement",
        title="Wave hits cost outcome",
    )
    db_session.add(hypothesis)
    await db_session.flush()

    event = await _seed_event(db_session)
    attached_a = await _seed_atom(db_session, event_id=event.id, atom_type="commitment")
    attached_b = await _seed_atom(db_session, event_id=event.id, atom_type="decision")
    unattached = await _seed_atom(db_session, event_id=event.id, atom_type="risk")

    db_session.add(
        AtomAttachment(
            id=str(ULID()),
            atom_id=attached_a.id,
            hypothesis_id=hypothesis.id,
            attached_by="human_confirmed",
        )
    )
    db_session.add(
        AtomAttachment(
            id=str(ULID()),
            atom_id=attached_b.id,
            hypothesis_id=hypothesis.id,
            attached_by="human_confirmed",
        )
    )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms?hypothesis_id={hypothesis.id}")

    assert resp.status_code == 200, resp.text
    returned_ids = {a["id"] for a in resp.json()["atoms"]}
    assert returned_ids == {attached_a.id, attached_b.id}
    assert unattached.id not in returned_ids


async def test_get_atom_with_decision_kind_returns_null_details(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms/{id} on a decision atom returns `details: null` —
    decision and status_update kinds have no detail table (DC7)."""
    event = await _seed_event(db_session)
    atom = await _seed_atom(
        db_session,
        event_id=event.id,
        atom_type="decision",
        content="Decided to use SQLite over Postgres for v1.",
    )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == "decision"
    assert data["details"] is None


async def test_get_atom_returns_atom_with_risk_details_using_mitigation_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms/{id} on a risk atom returns `mitigation_status` (NOT
    `current_status`). Pin 1 column-name asymmetry from #013 holds at the
    read-side dispatch in #016."""
    event = await _seed_event(db_session)
    atom = await _seed_atom(
        db_session,
        event_id=event.id,
        atom_type="risk",
        content="Steerco may delay budget approval.",
    )
    db_session.add(
        AtomRiskDetails(
            atom_id=atom.id,
            severity="high",
            mitigation_status="unmitigated",
        )
    )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == "risk"

    details = data["details"]
    assert details is not None
    assert details["mitigation_status"] == "unmitigated"
    assert details["severity"] == "high"
    assert details["owner_stakeholder_id"] is None
    # Pin 1 lock: risk uses `mitigation_status`, NOT `current_status`.
    assert "current_status" not in details


async def test_get_atom_returns_atom_with_event_source_and_commitment_details(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms/{id} returns the atom plus its nested event source and
    commitment-detail block (DC4, DC7). Source.kind='event'; details carry
    commitment fields."""
    stakeholder = Stakeholder(
        id=str(ULID()), canonical_name="Alice", primary_email="alice@example.com"
    )
    db_session.add(stakeholder)
    occurred = datetime(2026, 4, 19, 14, 0, 0, tzinfo=UTC)
    event = await _seed_event(
        db_session,
        event_type="process",
        occurred_at=occurred,
        source_path="inbox/work/transcripts/2026-04-19_steerco.md",
        body_summary="Steerco call.",
    )
    atom = await _seed_atom(
        db_session,
        event_id=event.id,
        atom_type="commitment",
        content="Alice will deliver the SOW.",
        confidence_sort_key=0.85,
    )
    db_session.add(
        AtomCommitmentDetails(
            atom_id=atom.id,
            owner_stakeholder_id=stakeholder.id,
            current_status="open",
        )
    )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == atom.id
    assert data["type"] == "commitment"
    assert data["content"] == "Alice will deliver the SOW."
    assert data["confidence_sort_key"] == 0.85
    assert data["dismissed"] is False
    assert data["retracted"] is False
    assert data["retracted_at"] is None

    source = data["source"]
    assert source["kind"] == "event"
    assert source["id"] == event.id
    assert source["type"] == "process"
    assert source["source_path"] == "inbox/work/transcripts/2026-04-19_steerco.md"
    assert source["body_summary"] == "Steerco call."
    assert source["occurred_at"].startswith("2026-04-19T14:00:00")

    details = data["details"]
    assert details is not None
    assert details["current_status"] == "open"
    assert details["owner_stakeholder_id"] == stakeholder.id
    assert details["due_date"] is None


async def test_get_provenance_returns_event_source_and_external_references(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms/{id}/provenance returns atom content + source envelope
    + linked external references via the atom_external_refs join (DC4)."""
    occurred = datetime(2026, 4, 19, 14, 0, 0, tzinfo=UTC)
    event = await _seed_event(
        db_session,
        event_type="process",
        occurred_at=occurred,
        source_path="inbox/work/transcripts/2026-04-19_steerco.md",
        body_summary="Steerco call.",
    )
    atom = await _seed_atom(
        db_session,
        event_id=event.id,
        atom_type="commitment",
        content="Alice will deliver the SOW.",
    )
    ref_a = ExternalReference(
        id=str(ULID()),
        ref_type="email_msgid",
        ref_value="<abc@example.com>",
        summary_md_path="outbox/work/external/abc.md",
    )
    ref_b = ExternalReference(
        id=str(ULID()),
        ref_type="url",
        ref_value="https://example.com/doc",
        summary_md_path=None,
    )
    db_session.add_all([ref_a, ref_b])
    await db_session.flush()
    db_session.add(AtomExternalRef(atom_id=atom.id, external_ref_id=ref_a.id))
    db_session.add(AtomExternalRef(atom_id=atom.id, external_ref_id=ref_b.id))
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}/provenance")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atom_id"] == atom.id
    assert data["content"] == "Alice will deliver the SOW."
    assert data["anchor_id"] == atom.anchor_id

    source = data["source"]
    assert source["kind"] == "event"
    assert source["id"] == event.id
    assert source["type"] == "process"
    assert source["source_path"] == "inbox/work/transcripts/2026-04-19_steerco.md"
    assert source["body_summary"] == "Steerco call."
    assert source["occurred_at"].startswith("2026-04-19T14:00:00")

    refs = data["external_references"]
    assert len(refs) == 2
    by_id = {r["id"]: r for r in refs}
    assert by_id[ref_a.id]["ref_type"] == "email_msgid"
    assert by_id[ref_a.id]["ref_value"] == "<abc@example.com>"
    assert by_id[ref_a.id]["summary_md_path"] == "outbox/work/external/abc.md"
    assert by_id[ref_b.id]["ref_type"] == "url"
    assert by_id[ref_b.id]["summary_md_path"] is None


async def test_get_provenance_returns_empty_external_references_when_none_attached(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Atom with no atom_external_refs rows yields `external_references: []`
    (empty list, not null)."""
    event = await _seed_event(db_session)
    atom = await _seed_atom(
        db_session, event_id=event.id, atom_type="decision", content="A decision."
    )
    await db_session.commit()

    resp = await client.get(f"/v1/atoms/{atom.id}/provenance")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["external_references"] == []
    assert data["source"]["kind"] == "event"
    assert data["source"]["id"] == event.id


@pytest.mark.visibility
async def test_get_atom_returns_404_for_atom_outside_audience_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms/{id} returns 404 for atoms invisible to the audience.

    Reuses get_atom from #013, which already 404s on invisible. Visibility
    invariant: invisible atoms cannot be revealed via direct ID lookup.
    """
    event = await _seed_event(db_session)
    private_atom = await _seed_atom(
        db_session, event_id=event.id, atom_type="decision", visibility_scope="private"
    )
    await db_session.commit()

    app.dependency_overrides[get_audience] = lambda: Audience.for_stakeholders(["s1"])
    try:
        resp = await client.get(f"/v1/atoms/{private_atom.id}")
        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_audience, None)


@pytest.mark.visibility
async def test_list_atoms_excludes_atoms_outside_audience_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /v1/atoms applies the visibility predicate per blueprint §6.4.

    A stakeholder audience that doesn't match an atom's visibility scope
    must NOT see that atom in list results. Verifies the full dependency
    wiring: get_audience → list_atoms predicate.
    """
    event = await _seed_event(db_session)
    public_a = await _seed_atom(
        db_session, event_id=event.id, atom_type="decision", visibility_scope="domain_wide"
    )
    public_b = await _seed_atom(
        db_session, event_id=event.id, atom_type="commitment", visibility_scope="domain_wide"
    )
    private_a = await _seed_atom(
        db_session, event_id=event.id, atom_type="decision", visibility_scope="private"
    )
    private_b = await _seed_atom(
        db_session, event_id=event.id, atom_type="risk", visibility_scope="private"
    )
    await db_session.commit()

    # Override the audience to a stakeholder set that doesn't intersect any
    # atom's stakeholder_set membership. Per visibility_predicate, this
    # audience sees only `domain_wide` atoms — not `private`.
    app.dependency_overrides[get_audience] = lambda: Audience.for_stakeholders(["s1"])
    try:
        resp = await client.get("/v1/atoms")
        assert resp.status_code == 200, resp.text
        ids = {a["id"] for a in resp.json()["atoms"]}

        # Private atoms must NOT leak.
        assert (
            private_a.id not in ids
        ), f"LEAK: private atom {private_a.id!r} visible to stakeholder audience"
        assert (
            private_b.id not in ids
        ), f"LEAK: private atom {private_b.id!r} visible to stakeholder audience"
        # domain_wide atoms MUST appear.
        assert public_a.id in ids
        assert public_b.id in ids
    finally:
        app.dependency_overrides.pop(get_audience, None)
