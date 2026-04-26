"""SQLAlchemy DeclarativeBase and ORM models.

W1 lands schema sections 1 (universal core) + 5 (operational tracking) per PRD §7.

Tables defined here (21 total):
  Section 1 — Universal core (18 tables):
    domains, arenas, engagements, hypotheses, hypothesis_state_changes,
    state_change_evidence, stakeholders, events, atoms,
    atom_commitment_details, atom_ask_details, atom_risk_details,
    atom_status_changes, atom_attachments, artifacts, artifact_versions,
    external_references, atom_external_refs

  Section 5 — Operational tracking (3 tables):
    triage_items, brief_runs, processor_runs

  Deferred to later workstreams (sections 2, 3, 4):
    entity_pages, tags, entity_tags  (section 2 — knowledge graph mirror)
    migration_records                (section 3 — migration tracking)
    work_account_metadata, work_engagement_metadata, work_stakeholder_roles,
    work_commitment_direction, work_ask_side  (section 4 — work projection)

Schema is locked in loom-meta/docs/loom-schema-v1.sql. Models must mirror it
exactly; deviations require an RFC.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from loom_core.storage.session import Base

# ---------------------------------------------------------------------------
# Section 1: Universal core
# ---------------------------------------------------------------------------


class Domain(Base):
    """Domains: first-class scope on every entity."""

    __tablename__ = "domains"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    privacy_tier: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "privacy_tier IN ('standard', 'sensitive')", name="ck_domains_privacy_tier"
        ),
    )


class Arena(Base):
    """Arenas: a logical grouping within a domain.

    Work: account. Finance: goal. Content: pillar. Code: project. (Work-only in v1.)
    """

    __tablename__ = "arenas"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (Index("idx_arenas_domain", "domain", "closed_at"),)


class Engagement(Base):
    """Engagements: bounded effort within an arena.

    Work: delivery wave, project, ongoing support.
    """

    __tablename__ = "engagements"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    arena_id: Mapped[str] = mapped_column(String(26), ForeignKey("arenas.id"))
    name: Mapped[str] = mapped_column(Text)
    type_tag: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        Index("idx_engagements_arena", "arena_id", "ended_at"),
        Index("idx_engagements_domain", "domain", "ended_at"),
    )


class Hypothesis(Base):
    """Hypotheses: value-anchored bets. Two layers.

    Engagement-level: arena_id and engagement_id both set, layer = 'engagement'.
    Arena-level:      arena_id set, engagement_id NULL,    layer = 'arena'.

    Current state is denormalized here for fast brief queries. The audit trail
    lives in hypothesis_state_changes; the denormalized columns must always
    reflect the latest change_at for each dimension.
    """

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    arena_id: Mapped[str] = mapped_column(String(26), ForeignKey("arenas.id"))
    engagement_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("engagements.id"))
    layer: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    current_progress: Mapped[str] = mapped_column(Text, server_default="'proposed'")
    current_confidence: Mapped[str] = mapped_column(Text, server_default="'medium'")
    current_momentum: Mapped[str] = mapped_column(Text, server_default="'steady'")
    progress_last_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    confidence_last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    momentum_last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    confidence_inferred: Mapped[bool] = mapped_column(Boolean, server_default="1")
    momentum_inferred: Mapped[bool] = mapped_column(Boolean, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint("layer IN ('arena', 'engagement')", name="ck_hypotheses_layer"),
        CheckConstraint(
            "current_progress IN ('proposed', 'in_delivery', 'realised', 'confirmed', 'dead')",
            name="ck_hypotheses_progress",
        ),
        CheckConstraint(
            "current_confidence IN ('low', 'medium', 'high')",
            name="ck_hypotheses_confidence",
        ),
        CheckConstraint(
            "current_momentum IN ('accelerating', 'steady', 'slowing', 'stalled')",
            name="ck_hypotheses_momentum",
        ),
        CheckConstraint(
            "(layer = 'arena' AND engagement_id IS NULL)"
            " OR (layer = 'engagement' AND engagement_id IS NOT NULL)",
            name="ck_hypotheses_layer_engagement",
        ),
        Index("idx_hypotheses_arena", "arena_id", "layer", "closed_at"),
        Index("idx_hypotheses_engagement", "engagement_id", "closed_at"),
        Index("idx_hypotheses_domain", "domain", "layer"),
    )


class HypothesisStateChange(Base):
    """Hypothesis state changes: immutable audit log.

    One row per dimension change. The latest row per (hypothesis_id, dimension)
    is the source of truth; the denormalized columns on hypotheses cache it.
    """

    __tablename__ = "hypothesis_state_changes"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    hypothesis_id: Mapped[str] = mapped_column(String(26), ForeignKey("hypotheses.id"))
    dimension: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    changed_by: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    override_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "dimension IN ('progress', 'confidence', 'momentum')",
            name="ck_hsc_dimension",
        ),
        CheckConstraint(
            "changed_by IN ('cron_inferred', 'human_confirmed', 'human_overridden')",
            name="ck_hsc_changed_by",
        ),
        Index(
            "idx_hsc_hypothesis",
            "hypothesis_id",
            "dimension",
            text("changed_at DESC"),
        ),
    )


class StateChangeEvidence(Base):
    """State-change evidence: many-to-many between state changes and triggering atoms.

    Provenance from a state transition back to the source atoms.
    atom_id FK was a forward declaration in the SQL (atoms defined later in the
    file); SQLAlchemy resolves creation order via the FK graph.
    """

    __tablename__ = "state_change_evidence"

    state_change_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("hypothesis_state_changes.id"), primary_key=True
    )
    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)


class Stakeholder(Base):
    """Stakeholders: global entities. Identity is global; roles are scoped per domain."""

    __tablename__ = "stakeholders"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    primary_email: Mapped[str | None] = mapped_column(Text, unique=True)
    aliases: Mapped[list[str] | None] = mapped_column(JSON)
    organization: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        Index("idx_stakeholders_name", text("canonical_name COLLATE NOCASE")),
        Index("idx_stakeholders_email", "primary_email"),
    )


class Event(Base):
    """Events: immutable journal. Once written, never edited."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    type: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    source_path: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    body_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "type IN ('process', 'inbox_derived', 'state_change',"
            " 'research', 'publication', 'external_reference')",
            name="ck_events_type",
        ),
        Index("idx_events_domain", "domain", text("occurred_at DESC")),
        Index("idx_events_type", "type", text("occurred_at DESC")),
    )


class Artifact(Base):
    """Artifacts: mutable, versioned workspaces (notebooks)."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    name: Mapped[str] = mapped_column(Text)
    type_tag: Mapped[str | None] = mapped_column(Text)
    parent_artifact_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("artifacts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    last_modified_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("idx_artifacts_domain", "domain", text("last_modified_at DESC")),)


class Atom(Base):
    """Atoms: extracted facts. The substrate.

    Every atom has a source: either an event (the journal) or an artifact
    (a notebook). At least one of event_id / artifact_id is non-null.

    Atoms carry visible block anchors (^d-001 etc.) into the rendered Obsidian
    event page. Wikilinks from hypothesis pages resolve to event_id#anchor_id.
    artifact_id FK was declared without REFERENCES in the SQL (artifacts table
    follows atoms); SQLAlchemy resolves creation order via the FK graph.
    """

    __tablename__ = "atoms"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    type: Mapped[str] = mapped_column(Text)
    event_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("events.id"))
    artifact_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("artifacts.id"))
    content: Mapped[str] = mapped_column(Text)
    anchor_id: Mapped[str] = mapped_column(Text)
    confidence_sort_key: Mapped[float | None] = mapped_column(Float, server_default="0.5")
    dismissed: Mapped[bool] = mapped_column(Boolean, server_default="0")
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissal_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "type IN ('decision', 'commitment', 'ask', 'risk', 'status_update')",
            name="ck_atoms_type",
        ),
        CheckConstraint(
            "confidence_sort_key BETWEEN 0 AND 1",
            name="ck_atoms_confidence_sort_key",
        ),
        CheckConstraint(
            "event_id IS NOT NULL OR artifact_id IS NOT NULL",
            name="ck_atoms_source",
        ),
        Index("idx_atoms_event", "event_id"),
        Index("idx_atoms_artifact", "artifact_id"),
        Index(
            "idx_atoms_type",
            "domain",
            "type",
            "dismissed",
            text("created_at DESC"),
        ),
        Index("idx_atoms_dismissed", "dismissed", text("created_at DESC")),
        Index(
            "idx_atoms_anchor_event",
            "event_id",
            "anchor_id",
            unique=True,
            sqlite_where=text("event_id IS NOT NULL"),
        ),
        Index(
            "idx_atoms_anchor_artifact",
            "artifact_id",
            "anchor_id",
            unique=True,
            sqlite_where=text("artifact_id IS NOT NULL"),
        ),
    )


class AtomCommitmentDetails(Base):
    """Atom type-specific details for commitment atoms (1:1 with atom)."""

    __tablename__ = "atom_commitment_details"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    owner_stakeholder_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("stakeholders.id")
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    current_status: Mapped[str] = mapped_column(Text, server_default="'open'")
    status_last_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "current_status IN"
            " ('open', 'in_progress', 'met', 'slipped', 'renegotiated', 'cancelled')",
            name="ck_commit_status",
        ),
        Index("idx_commit_owner", "owner_stakeholder_id", "current_status"),
        Index("idx_commit_status", "current_status", "due_date"),
        Index(
            "idx_commit_due",
            "due_date",
            sqlite_where=text("current_status NOT IN ('met', 'cancelled')"),
        ),
    )


class AtomAskDetails(Base):
    """Atom type-specific details for ask atoms (1:1 with atom).

    The owner of an ask is the party who owes the answer/action — inverted vs commitment.
    """

    __tablename__ = "atom_ask_details"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    owner_stakeholder_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("stakeholders.id")
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    current_status: Mapped[str] = mapped_column(Text, server_default="'raised'")
    status_last_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "current_status IN ('raised', 'acknowledged', 'in_progress', 'granted', 'declined')",
            name="ck_ask_status",
        ),
        Index("idx_ask_owner", "owner_stakeholder_id", "current_status"),
        Index("idx_ask_status", "current_status"),
    )


class AtomRiskDetails(Base):
    """Atom type-specific details for risk atoms (1:1 with atom)."""

    __tablename__ = "atom_risk_details"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    severity: Mapped[str] = mapped_column(Text)
    owner_stakeholder_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("stakeholders.id")
    )
    mitigation_status: Mapped[str] = mapped_column(Text, server_default="'unmitigated'")

    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_severity",
        ),
        CheckConstraint(
            "mitigation_status IN"
            " ('unmitigated', 'mitigation_in_progress', 'mitigated', 'accepted')",
            name="ck_risk_mitigation_status",
        ),
        Index("idx_risk_severity", "severity", "mitigation_status"),
    )


class AtomStatusChange(Base):
    """Atom status change history for commitments, asks, and risks.

    One row per status transition; latest reflects current_status on the detail tables.
    """

    __tablename__ = "atom_status_changes"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"))
    old_status: Mapped[str | None] = mapped_column(Text)
    new_status: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    changed_by: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("idx_atom_status_atom", "atom_id", text("changed_at DESC")),)


class AtomAttachment(Base):
    """Atom attachments: the triage decision.

    An atom can attach to multiple hypotheses. Dismissals retained as training signal.
    """

    __tablename__ = "atom_attachments"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"))
    hypothesis_id: Mapped[str] = mapped_column(String(26), ForeignKey("hypotheses.id"))
    attached_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    attached_by: Mapped[str] = mapped_column(Text)
    ambiguity_flag: Mapped[bool] = mapped_column(Boolean, server_default="0")
    dismissed: Mapped[bool] = mapped_column(Boolean, server_default="0")
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissal_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "attached_by IN ('cron_suggested', 'human_confirmed')",
            name="ck_attach_attached_by",
        ),
        UniqueConstraint("atom_id", "hypothesis_id", name="uq_atom_attachments"),
        Index(
            "idx_attach_hypothesis",
            "hypothesis_id",
            "dismissed",
            text("attached_at DESC"),
        ),
        Index("idx_attach_atom", "atom_id", "dismissed"),
    )


class ArtifactVersion(Base):
    """Artifact versions: immutable snapshots of a versioned workspace."""

    __tablename__ = "artifact_versions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(26), ForeignKey("artifacts.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    content_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    authorship: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "authorship IN ('human', 'claude', 'collaborative')",
            name="ck_av_authorship",
        ),
        UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions"),
        Index("idx_av_artifact", "artifact_id", text("version_number DESC")),
    )


class ExternalReference(Base):
    """External references: live link plus snapshot summary.

    The summary itself lives as markdown in Obsidian (summary_md_path);
    the row here is the index entry plus the live pointer.
    """

    __tablename__ = "external_references"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    ref_type: Mapped[str] = mapped_column(Text)
    ref_value: Mapped[str] = mapped_column(Text)
    summary_md_path: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    unreachable: Mapped[bool] = mapped_column(Boolean, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "ref_type IN ('url', 'email_msgid', 'git_commit', 'sharepoint', 'gdrive')",
            name="ck_extref_ref_type",
        ),
        UniqueConstraint("ref_type", "ref_value", name="uq_external_references"),
        Index("idx_extref_unreachable", "unreachable", "last_verified_at"),
    )


class AtomExternalRef(Base):
    """Atoms can cite external references (many-to-many)."""

    __tablename__ = "atom_external_refs"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    external_ref_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("external_references.id"), primary_key=True
    )


# ---------------------------------------------------------------------------
# Section 5: Operational tracking
# ---------------------------------------------------------------------------


class TriageItem(Base):
    """Triage items: anything awaiting human review. The Friday 4-5pm queue.

    related_entity_type and related_entity_id are polymorphic references —
    no ForeignKey is declared; Loom Core enforces referential integrity at
    the service layer (SQLite cannot enforce polymorphic FKs).
    """

    __tablename__ = "triage_items"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    item_type: Mapped[str] = mapped_column(Text)
    # Polymorphic reference — no FK; service layer enforces integrity.
    related_entity_type: Mapped[str] = mapped_column(Text)
    # Polymorphic reference — no FK; service layer enforces integrity.
    related_entity_id: Mapped[str] = mapped_column(Text)
    surfaced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution: Mapped[str | None] = mapped_column(Text)
    priority_score: Mapped[float | None] = mapped_column(Float, server_default="0.5")
    context_summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('state_change_proposal', 'low_confidence_atom',"
            " 'ambiguous_routing', 'migration_review', 'stakeholder_resolution')",
            name="ck_triage_item_type",
        ),
        Index(
            "idx_triage_pending",
            "item_type",
            text("surfaced_at DESC"),
            sqlite_where=text("resolved_at IS NULL"),
        ),
        Index("idx_triage_entity", "related_entity_type", "related_entity_id"),
    )


class BriefRun(Base):
    """Brief generation log: what ran when, output, success/failure."""

    __tablename__ = "brief_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    brief_type: Mapped[str] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[str] = mapped_column(Text)
    output_path: Mapped[str] = mapped_column(Text)
    ran_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, server_default="1")
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "brief_type IN ('engagement_daily', 'arena_weekly')",
            name="ck_brief_type",
        ),
        Index("idx_briefs_scope", "scope_type", "scope_id", text("ran_at DESC")),
    )


class ProcessorRun(Base):
    """Processor run log: the cron pipeline. Useful for observability."""

    __tablename__ = "processor_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    pipeline: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    items_processed: Mapped[int | None] = mapped_column(Integer)
    items_failed: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "pipeline IN ('inbox_sweep', 'migration_batch', 'state_inference',"
            " 'kg_render', 'brief_generation')",
            name="ck_proc_pipeline",
        ),
        Index("idx_proc_runs", "pipeline", text("started_at DESC")),
    )


__all__ = [
    "Artifact",
    "ArtifactVersion",
    "Atom",
    "AtomAskDetails",
    "AtomAttachment",
    "AtomCommitmentDetails",
    "AtomExternalRef",
    "AtomRiskDetails",
    "AtomStatusChange",
    "Base",
    "BriefRun",
    "Domain",
    "Arena",
    "Engagement",
    "Event",
    "ExternalReference",
    "Hypothesis",
    "HypothesisStateChange",
    "ProcessorRun",
    "Stakeholder",
    "StateChangeEvidence",
    "TriageItem",
]
