"""SQLAlchemy DeclarativeBase and ORM models.

W1 lands schema sections 1 (universal core) + 5 (operational tracking) per PRD §7.

Tables defined here (31 total):
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
    work_account_metadata, work_engagement_metadata,
    work_commitment_direction, work_ask_side  (section 4 — work projection)

v0.8 alignment additions (#076):
  Cross-cutting columns on entity tables (visibility_scope, retention_tier,
  projection_at_creation per blueprint §6.4 / §12.6 / §4).
  Inference metadata on atoms, hypothesis_state_changes, brief_runs.
  Retraction columns on atoms; audience profile on stakeholders.
  ProcessorRun.success column folded in from #009.
  New tables: entity_visibility_members, stakeholder_roles,
  atom_contributions, resources, resource_attributions, asset_uses.

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
    projection_at_creation: Mapped[str] = mapped_column(
        Text, server_default="'work-cro-1cloudhub-v1'"
    )
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
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    projection_at_creation: Mapped[str] = mapped_column(
        Text, server_default="'work-cro-1cloudhub-v1'"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_engagements_retention_tier",
        ),
        Index("idx_engagements_arena", "arena_id", "ended_at"),
        Index("idx_engagements_domain", "domain", "ended_at"),
        Index("idx_engagements_retention", "retention_tier"),
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
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    projection_at_creation: Mapped[str] = mapped_column(
        Text, server_default="'work-cro-1cloudhub-v1'"
    )
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
        CheckConstraint(
            "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')",
            name="ck_hypotheses_visibility_scope",
        ),
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_hypotheses_retention_tier",
        ),
        Index("idx_hypotheses_arena", "arena_id", "layer", "closed_at"),
        Index("idx_hypotheses_engagement", "engagement_id", "closed_at"),
        Index("idx_hypotheses_domain", "domain", "layer"),
        Index("idx_hypotheses_retention", "retention_tier"),
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
    inference_provider: Mapped[str | None] = mapped_column(Text)
    inference_model_version: Mapped[str | None] = mapped_column(Text)
    inference_skill_version: Mapped[str | None] = mapped_column(Text)

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
    audience_schema: Mapped[str | None] = mapped_column(Text)
    preferred_depth: Mapped[str | None] = mapped_column(Text)
    preferred_channel: Mapped[str | None] = mapped_column(Text)
    tone_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "audience_schema IS NULL OR audience_schema IN"
            " ('executive', 'technical', 'aws_partner', 'customer_sponsor', 'visual')",
            name="ck_stakeholders_audience_schema",
        ),
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
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    projection_at_creation: Mapped[str] = mapped_column(
        Text, server_default="'work-cro-1cloudhub-v1'"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "type IN ('process', 'inbox_derived', 'state_change',"
            " 'research', 'publication', 'external_reference')",
            name="ck_events_type",
        ),
        CheckConstraint(
            "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')",
            name="ck_events_visibility_scope",
        ),
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_events_retention_tier",
        ),
        Index("idx_events_domain", "domain", text("occurred_at DESC")),
        Index("idx_events_type", "type", text("occurred_at DESC")),
        Index("idx_events_retention", "retention_tier"),
    )


class Artifact(Base):
    """Artifacts: mutable, versioned workspaces (notebooks)."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    name: Mapped[str] = mapped_column(Text)
    type_tag: Mapped[str | None] = mapped_column(Text)
    parent_artifact_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("artifacts.id"))
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    projection_at_creation: Mapped[str] = mapped_column(
        Text, server_default="'work-cro-1cloudhub-v1'"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    last_modified_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')",
            name="ck_artifacts_visibility_scope",
        ),
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_artifacts_retention_tier",
        ),
        Index("idx_artifacts_domain", "domain", text("last_modified_at DESC")),
        Index("idx_artifacts_retention", "retention_tier"),
    )


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
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    projection_at_creation: Mapped[str] = mapped_column(
        Text, server_default="'work-cro-1cloudhub-v1'"
    )
    extractor_provider: Mapped[str | None] = mapped_column(Text)
    extractor_model_version: Mapped[str | None] = mapped_column(Text)
    extractor_skill_version: Mapped[str | None] = mapped_column(Text)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    source_span_start: Mapped[int | None] = mapped_column(Integer)
    source_span_end: Mapped[int | None] = mapped_column(Integer)
    retracted: Mapped[bool] = mapped_column(Boolean, server_default="0")
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime)
    retraction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "type IN ('decision', 'commitment', 'ask', 'risk', 'status_update')",
            name="ck_atoms_type",
        ),
        CheckConstraint(
            "extractor_provider IS NULL OR extractor_provider IN"
            " ('python_rules', 'embeddings', 'apple_fm', 'claude_api', 'human')",
            name="ck_atoms_extractor_provider",
        ),
        CheckConstraint(
            "retraction_reason IS NULL OR retraction_reason IN"
            " ('hallucination', 'wrong_extraction', 'stale_source', 'corrected_on_review')",
            name="ck_atoms_retraction_reason",
        ),
        CheckConstraint(
            "confidence_sort_key BETWEEN 0 AND 1",
            name="ck_atoms_confidence_sort_key",
        ),
        CheckConstraint(
            "event_id IS NOT NULL OR artifact_id IS NOT NULL",
            name="ck_atoms_source",
        ),
        CheckConstraint(
            "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')",
            name="ck_atoms_visibility_scope",
        ),
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_atoms_retention_tier",
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
        Index("idx_atoms_retention", "retention_tier"),
        Index("idx_atoms_retracted", "retracted", sqlite_where=text("retracted = 1")),
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
    authorship: Mapped[str | None] = mapped_column(Text)
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "authorship IN ('human', 'claude', 'collaborative')",
            name="ck_av_authorship",
        ),
        CheckConstraint(
            "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')",
            name="ck_artifact_versions_visibility_scope",
        ),
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_artifact_versions_retention_tier",
        ),
        UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions"),
        Index("idx_av_artifact", "artifact_id", text("version_number DESC")),
        Index("idx_artifact_versions_retention", "retention_tier"),
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
    unreachable: Mapped[bool] = mapped_column(Boolean, server_default="0")
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    retention_tier: Mapped[str] = mapped_column(Text, server_default="'operational'")
    captured_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "ref_type IN ('url', 'email_msgid', 'git_commit', 'sharepoint', 'gdrive')",
            name="ck_extref_ref_type",
        ),
        CheckConstraint(
            "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')",
            name="ck_external_references_visibility_scope",
        ),
        CheckConstraint(
            "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')",
            name="ck_external_references_retention_tier",
        ),
        UniqueConstraint("ref_type", "ref_value", name="uq_external_references"),
        Index("idx_extref_unreachable", "unreachable", "last_verified_at"),
        Index("idx_external_references_retention", "retention_tier"),
    )


class AtomExternalRef(Base):
    """Atoms can cite external references (many-to-many)."""

    __tablename__ = "atom_external_refs"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    external_ref_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("external_references.id"), primary_key=True
    )


# ---------------------------------------------------------------------------
# v0.8 alignment: visibility membership, stakeholder roles, forward provenance
# ---------------------------------------------------------------------------


class EntityVisibilityMember(Base):
    """Stakeholder-set visibility membership (link table per blueprint §6.4).

    Composite PK over (entity_type, entity_id, stakeholder_id). entity_id is a
    polymorphic reference; integrity is enforced at the service layer.
    """

    __tablename__ = "entity_visibility_members"

    entity_type: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    stakeholder_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("stakeholders.id"), primary_key=True
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('event', 'atom', 'hypothesis',"
            " 'artifact', 'artifact_version', 'external_reference')",
            name="ck_evm_entity_type",
        ),
        Index("idx_evm_lookup", "entity_type", "entity_id"),
    )


class StakeholderRole(Base):
    """Time-bounded stakeholder role periods per blueprint §4 (Stakeholders).

    Replaces the work-specific work_stakeholder_roles (dropped in #076). Role
    values come from the universal 10-role enum. scope_id is polymorphic.
    """

    __tablename__ = "stakeholder_roles"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    stakeholder_id: Mapped[str] = mapped_column(String(26), ForeignKey("stakeholders.id"))
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    scope_type: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[str | None] = mapped_column(String(26))
    role: Mapped[str] = mapped_column(Text)
    started_at: Mapped[date] = mapped_column(Date)
    ended_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('arena', 'engagement', 'domain')",
            name="ck_sr_scope_type",
        ),
        CheckConstraint(
            "role IN ('sponsor', 'beneficiary', 'blocker', 'validator',"
            " 'advocate', 'doer', 'influencer', 'advisor',"
            " 'decision_maker', 'informed_party')",
            name="ck_sr_role",
        ),
        Index(
            "idx_sr_current",
            "stakeholder_id",
            "scope_id",
            sqlite_where=text("ended_at IS NULL"),
        ),
        Index("idx_sr_scope", "scope_type", "scope_id", "ended_at"),
    )


class AtomContribution(Base):
    """Forward-provenance index: atom → consumer per refactor plan §4.1.

    Composite PK (atom_id, consumer_type, consumer_id). consumer_id is
    polymorphic; integrity enforced at service layer.
    """

    __tablename__ = "atom_contributions"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    consumer_type: Mapped[str] = mapped_column(Text, primary_key=True)
    consumer_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    contributed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint(
            "consumer_type IN ('brief_run', 'state_change',"
            " 'draft', 'sent_action', 'derived_atom')",
            name="ck_ac_consumer_type",
        ),
        Index("idx_ac_consumer", "consumer_type", "consumer_id"),
    )


# ---------------------------------------------------------------------------
# v0.8 alignment: Resources / Leverage layer (blueprint §4 — Resources)
# ---------------------------------------------------------------------------


class Resource(Base):
    """Leverage entity per blueprint §4 (Resources, the fourth atomic unit).

    Seven category enum: time, people, financial, attention, credibility,
    knowledge_asset, tooling_asset. Inferred-first discipline: inferred_from
    distinguishes auto-inferred vs manual entry.
    """

    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    domain: Mapped[str] = mapped_column(String(26), ForeignKey("domains.id"))
    category: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Float)
    quantity_unit: Mapped[str | None] = mapped_column(Text)
    quality_dimensions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    expiry_at: Mapped[date | None] = mapped_column(Date)
    replenishment_rule: Mapped[str | None] = mapped_column(Text)
    inferred_from: Mapped[str | None] = mapped_column(Text)
    visibility_scope: Mapped[str] = mapped_column(Text, server_default="'private'")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint(
            "category IN ('time', 'people', 'financial', 'attention',"
            " 'credibility', 'knowledge_asset', 'tooling_asset')",
            name="ck_resources_category",
        ),
        CheckConstraint(
            "inferred_from IS NULL OR inferred_from IN"
            " ('calendar_density', 'mailbox_traffic', 'expense_reports',"
            " 'response_patterns', 'usage_logs', 'manual')",
            name="ck_resources_inferred_from",
        ),
        Index("idx_resources_category", "domain", "category"),
        Index(
            "idx_resources_expiry",
            "expiry_at",
            sqlite_where=text("expiry_at IS NOT NULL"),
        ),
    )


class ResourceAttribution(Base):
    """Resource → consumer attribution per refactor plan §1.8."""

    __tablename__ = "resource_attributions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(26), ForeignKey("resources.id"))
    consumer_type: Mapped[str] = mapped_column(Text)
    consumer_id: Mapped[str] = mapped_column(String(26))
    quantity: Mapped[float] = mapped_column(Float)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    attributed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint(
            "consumer_type IN ('hypothesis', 'engagement', 'draft', 'sent_action')",
            name="ck_ra_consumer_type",
        ),
        Index("idx_ra_resource", "resource_id", "released_at"),
        Index("idx_ra_consumer", "consumer_type", "consumer_id"),
    )


class AssetUse(Base):
    """Knowledge / tooling asset saturation tracking per refactor plan §1.8."""

    __tablename__ = "asset_uses"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(26), ForeignKey("resources.id"))
    audience_type: Mapped[str] = mapped_column(Text)
    used_in_consumer_type: Mapped[str] = mapped_column(Text)
    used_in_consumer_id: Mapped[str] = mapped_column(String(26))
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    __table_args__ = (Index("idx_asset_uses", "resource_id", "audience_type"),)


# ---------------------------------------------------------------------------
# Section 4: Work projection (progressive — work_account_metadata landed in
# W2 #002; work_engagement_metadata + atom extensions land in W2 #003)
# ---------------------------------------------------------------------------


class WorkAccountMetadata(Base):
    """Work-domain account metadata (extends arenas).

    One optional row per arena — arena_id is both PK and FK.
    """

    __tablename__ = "work_account_metadata"

    arena_id: Mapped[str] = mapped_column(String(26), ForeignKey("arenas.id"), primary_key=True)
    industry: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    aws_segment: Mapped[str | None] = mapped_column(Text)
    customer_type: Mapped[str | None] = mapped_column(Text)


class WorkEngagementMetadata(Base):
    """Work-domain engagement metadata (extends engagements).

    One optional row per engagement — engagement_id is both PK and FK.
    """

    __tablename__ = "work_engagement_metadata"

    engagement_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("engagements.id"), primary_key=True
    )
    sow_value: Mapped[float | None] = mapped_column(Float)
    sow_currency: Mapped[str | None] = mapped_column(Text)
    aws_funded: Mapped[bool] = mapped_column(Boolean, server_default="0")
    aws_program: Mapped[str | None] = mapped_column(Text)
    swim_lane: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "swim_lane IN ('p1_existing_customer', 'p2_sales_generated',"
            " 'p3_demand_gen_sdr', 'p4_aws_referral')",
            name="ck_wem_swim_lane",
        ),
    )


class WorkCommitmentDirection(Base):
    """Work-domain commitment direction — actor topology for CRO motion.

    Populated once atoms exist (W3+). Migrated here to keep section 4 complete.
    """

    __tablename__ = "work_commitment_direction"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    direction: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "direction IN ('1ch_to_customer', 'customer_to_1ch',"
            " '1ch_to_aws', 'aws_to_1ch',"
            " 'customer_to_aws', 'aws_to_customer', '1ch_internal')",
            name="ck_wcd_direction",
        ),
        Index("idx_wcd_direction", "direction"),
    )


class WorkAskSide(Base):
    """Work-domain ask side.

    Populated once atoms exist (W3+). Migrated here to keep section 4 complete.
    """

    __tablename__ = "work_ask_side"

    atom_id: Mapped[str] = mapped_column(String(26), ForeignKey("atoms.id"), primary_key=True)
    side: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "side IN ('asks_of_aws', 'asks_of_customer', 'asks_of_1cloudhub')",
            name="ck_was_side",
        ),
        Index("idx_was_side", "side"),
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
    composer_skill_version: Mapped[str | None] = mapped_column(Text)
    provider_chain: Mapped[list[str] | None] = mapped_column(JSON)

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
    success: Mapped[bool] = mapped_column(Boolean, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "pipeline IN ('inbox_sweep', 'migration_batch', 'state_inference',"
            " 'kg_render', 'brief_generation')",
            name="ck_proc_pipeline",
        ),
        Index("idx_proc_runs", "pipeline", text("started_at DESC")),
    )


__all__ = [
    "Arena",
    "Artifact",
    "ArtifactVersion",
    "AssetUse",
    "Atom",
    "AtomAskDetails",
    "AtomAttachment",
    "AtomCommitmentDetails",
    "AtomContribution",
    "AtomExternalRef",
    "AtomRiskDetails",
    "AtomStatusChange",
    "Base",
    "BriefRun",
    "Domain",
    "Engagement",
    "EntityVisibilityMember",
    "Event",
    "ExternalReference",
    "Hypothesis",
    "HypothesisStateChange",
    "ProcessorRun",
    "Resource",
    "ResourceAttribution",
    "Stakeholder",
    "StakeholderRole",
    "StateChangeEvidence",
    "TriageItem",
    "WorkAccountMetadata",
    "WorkAskSide",
    "WorkCommitmentDirection",
    "WorkEngagementMetadata",
]
