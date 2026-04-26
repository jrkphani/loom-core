"""W1: universal core and operational tracking

Revision ID: bf4d89061130
Revises:
Create Date: 2026-04-26 19:25:15.902790

Hand-reviewed against loom-meta/docs/loom-schema-v1.sql sections 1 + 5.
Changes vs raw autogenerate:
  - Fixed 6 string server_default values that lost their SQL string quotes
    (autogenerate strips inner quotes; corrected to sa.text("'value'")).
  - Added 13 expression-based indexes skipped by autogenerate (all involve
    DESC ordering or COLLATE NOCASE, which SQLAlchemy 2.0.49 cannot reflect
    for SQLite and therefore omits from the autogenerate diff).
  - Added domains seed row ('work', 'Work / CRO', 'standard').
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf4d89061130"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brief_runs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("brief_type", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=False),
        sa.Column(
            "ran_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "brief_type IN ('engagement_daily', 'arena_weekly')", name="ck_brief_type"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Skipped by autogenerate (expression-based index with DESC column).
    op.create_index(
        "idx_briefs_scope",
        "brief_runs",
        ["scope_type", "scope_id", sa.text("ran_at DESC")],
    )

    op.create_table(
        "domains",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("privacy_tier", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "privacy_tier IN ('standard', 'sensitive')", name="ck_domains_privacy_tier"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seed the v1 work domain (loom-schema-v1.sql line ~36).
    op.execute(
        "INSERT INTO domains (id, display_name, privacy_tier)"
        " VALUES ('work', 'Work / CRO', 'standard')"
    )

    op.create_table(
        "external_references",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("ref_type", sa.Text(), nullable=False),
        sa.Column("ref_value", sa.Text(), nullable=False),
        sa.Column("summary_md_path", sa.Text(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("unreachable", sa.Boolean(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "ref_type IN ('url', 'email_msgid', 'git_commit', 'sharepoint', 'gdrive')",
            name="ck_extref_ref_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ref_type", "ref_value", name="uq_external_references"),
    )
    with op.batch_alter_table("external_references", schema=None) as batch_op:
        batch_op.create_index(
            "idx_extref_unreachable", ["unreachable", "last_verified_at"], unique=False
        )

    op.create_table(
        "processor_runs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("pipeline", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("items_processed", sa.Integer(), nullable=True),
        sa.Column("items_failed", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "pipeline IN ('inbox_sweep', 'migration_batch', 'state_inference',"
            " 'kg_render', 'brief_generation')",
            name="ck_proc_pipeline",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Skipped by autogenerate (expression-based index with DESC column).
    op.create_index(
        "idx_proc_runs",
        "processor_runs",
        ["pipeline", sa.text("started_at DESC")],
    )

    op.create_table(
        "stakeholders",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("primary_email", sa.Text(), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("primary_email"),
    )
    with op.batch_alter_table("stakeholders", schema=None) as batch_op:
        batch_op.create_index("idx_stakeholders_email", ["primary_email"], unique=False)
    # Skipped by autogenerate (expression-based index with COLLATE NOCASE).
    op.create_index(
        "idx_stakeholders_name",
        "stakeholders",
        [sa.text("canonical_name COLLATE NOCASE")],
    )

    op.create_table(
        "triage_items",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("related_entity_type", sa.Text(), nullable=False),
        sa.Column("related_entity_id", sa.Text(), nullable=False),
        sa.Column(
            "surfaced_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("priority_score", sa.Float(), server_default="0.5", nullable=True),
        sa.Column("context_summary", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "item_type IN ('state_change_proposal', 'low_confidence_atom',"
            " 'ambiguous_routing', 'migration_review', 'stakeholder_resolution')",
            name="ck_triage_item_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("triage_items", schema=None) as batch_op:
        batch_op.create_index(
            "idx_triage_entity",
            ["related_entity_type", "related_entity_id"],
            unique=False,
        )
    # Skipped by autogenerate (partial index with DESC column).
    op.create_index(
        "idx_triage_pending",
        "triage_items",
        ["item_type", sa.text("surfaced_at DESC")],
        sqlite_where=sa.text("resolved_at IS NULL"),
    )

    op.create_table(
        "arenas",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("domain", sa.String(length=26), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["domain"],
            ["domains.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("arenas", schema=None) as batch_op:
        batch_op.create_index("idx_arenas_domain", ["domain", "closed_at"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("domain", sa.String(length=26), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type_tag", sa.Text(), nullable=True),
        sa.Column("parent_artifact_id", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "last_modified_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["domain"],
            ["domains.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_artifact_id"],
            ["artifacts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Skipped by autogenerate (expression-based index with DESC column).
    op.create_index(
        "idx_artifacts_domain",
        "artifacts",
        ["domain", sa.text("last_modified_at DESC")],
    )

    op.create_table(
        "events",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("domain", sa.String(length=26), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.Column("body_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('process', 'inbox_derived', 'state_change',"
            " 'research', 'publication', 'external_reference')",
            name="ck_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["domain"],
            ["domains.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Skipped by autogenerate (expression-based indexes with DESC column).
    op.create_index("idx_events_domain", "events", ["domain", sa.text("occurred_at DESC")])
    op.create_index("idx_events_type", "events", ["type", sa.text("occurred_at DESC")])

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("artifact_id", sa.String(length=26), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_path", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("authorship", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "authorship IN ('human', 'claude', 'collaborative')", name="ck_av_authorship"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions"),
    )
    # Skipped by autogenerate (expression-based index with DESC column).
    op.create_index(
        "idx_av_artifact",
        "artifact_versions",
        ["artifact_id", sa.text("version_number DESC")],
    )

    op.create_table(
        "atoms",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("domain", sa.String(length=26), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("event_id", sa.String(length=26), nullable=True),
        sa.Column("artifact_id", sa.String(length=26), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("anchor_id", sa.Text(), nullable=False),
        sa.Column("confidence_sort_key", sa.Float(), server_default="0.5", nullable=True),
        sa.Column("dismissed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("dismissal_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('decision', 'commitment', 'ask', 'risk', 'status_update')",
            name="ck_atoms_type",
        ),
        sa.CheckConstraint(
            "confidence_sort_key BETWEEN 0 AND 1", name="ck_atoms_confidence_sort_key"
        ),
        sa.CheckConstraint(
            "event_id IS NOT NULL OR artifact_id IS NOT NULL", name="ck_atoms_source"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["domain"],
            ["domains.id"],
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("atoms", schema=None) as batch_op:
        batch_op.create_index(
            "idx_atoms_anchor_artifact",
            ["artifact_id", "anchor_id"],
            unique=True,
            sqlite_where=sa.text("artifact_id IS NOT NULL"),
        )
        batch_op.create_index(
            "idx_atoms_anchor_event",
            ["event_id", "anchor_id"],
            unique=True,
            sqlite_where=sa.text("event_id IS NOT NULL"),
        )
        batch_op.create_index("idx_atoms_artifact", ["artifact_id"], unique=False)
        batch_op.create_index("idx_atoms_event", ["event_id"], unique=False)
        # Skipped by autogenerate (expression-based indexes with DESC column).
        batch_op.create_index(
            "idx_atoms_dismissed", ["dismissed", sa.text("created_at DESC")], unique=False
        )
        batch_op.create_index(
            "idx_atoms_type",
            ["domain", "type", "dismissed", sa.text("created_at DESC")],
            unique=False,
        )

    op.create_table(
        "engagements",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("domain", sa.String(length=26), nullable=False),
        sa.Column("arena_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type_tag", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["arena_id"],
            ["arenas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["domain"],
            ["domains.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("engagements", schema=None) as batch_op:
        batch_op.create_index("idx_engagements_arena", ["arena_id", "ended_at"], unique=False)
        batch_op.create_index("idx_engagements_domain", ["domain", "ended_at"], unique=False)

    op.create_table(
        "atom_ask_details",
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("owner_stakeholder_id", sa.String(length=26), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        # Autogenerate stripped inner SQL quotes; corrected to sa.text("'raised'").
        sa.Column(
            "current_status",
            sa.Text(),
            server_default=sa.text("'raised'"),
            nullable=False,
        ),
        sa.Column(
            "status_last_changed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "current_status IN ('raised', 'acknowledged', 'in_progress', 'granted', 'declined')",
            name="ck_ask_status",
        ),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_stakeholder_id"],
            ["stakeholders.id"],
        ),
        sa.PrimaryKeyConstraint("atom_id"),
    )
    with op.batch_alter_table("atom_ask_details", schema=None) as batch_op:
        batch_op.create_index(
            "idx_ask_owner", ["owner_stakeholder_id", "current_status"], unique=False
        )
        batch_op.create_index("idx_ask_status", ["current_status"], unique=False)

    op.create_table(
        "atom_commitment_details",
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("owner_stakeholder_id", sa.String(length=26), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        # Autogenerate stripped inner SQL quotes; corrected to sa.text("'open'").
        sa.Column(
            "current_status",
            sa.Text(),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column(
            "status_last_changed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "current_status IN"
            " ('open', 'in_progress', 'met', 'slipped', 'renegotiated', 'cancelled')",
            name="ck_commit_status",
        ),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_stakeholder_id"],
            ["stakeholders.id"],
        ),
        sa.PrimaryKeyConstraint("atom_id"),
    )
    with op.batch_alter_table("atom_commitment_details", schema=None) as batch_op:
        batch_op.create_index(
            "idx_commit_due",
            ["due_date"],
            unique=False,
            sqlite_where=sa.text("current_status NOT IN ('met', 'cancelled')"),
        )
        batch_op.create_index(
            "idx_commit_owner", ["owner_stakeholder_id", "current_status"], unique=False
        )
        batch_op.create_index("idx_commit_status", ["current_status", "due_date"], unique=False)

    op.create_table(
        "atom_external_refs",
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("external_ref_id", sa.String(length=26), nullable=False),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["external_ref_id"],
            ["external_references.id"],
        ),
        sa.PrimaryKeyConstraint("atom_id", "external_ref_id"),
    )

    op.create_table(
        "atom_risk_details",
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("owner_stakeholder_id", sa.String(length=26), nullable=True),
        # Autogenerate stripped inner SQL quotes; corrected to sa.text("'unmitigated'").
        sa.Column(
            "mitigation_status",
            sa.Text(),
            server_default=sa.text("'unmitigated'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mitigation_status IN"
            " ('unmitigated', 'mitigation_in_progress', 'mitigated', 'accepted')",
            name="ck_risk_mitigation_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')", name="ck_risk_severity"
        ),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_stakeholder_id"],
            ["stakeholders.id"],
        ),
        sa.PrimaryKeyConstraint("atom_id"),
    )
    with op.batch_alter_table("atom_risk_details", schema=None) as batch_op:
        batch_op.create_index("idx_risk_severity", ["severity", "mitigation_status"], unique=False)

    op.create_table(
        "atom_status_changes",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("old_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Skipped by autogenerate (expression-based index with DESC column).
    op.create_index(
        "idx_atom_status_atom",
        "atom_status_changes",
        ["atom_id", sa.text("changed_at DESC")],
    )

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("domain", sa.String(length=26), nullable=False),
        sa.Column("arena_id", sa.String(length=26), nullable=False),
        sa.Column("engagement_id", sa.String(length=26), nullable=True),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Autogenerate stripped inner SQL quotes; corrected to sa.text("'proposed'").
        sa.Column(
            "current_progress",
            sa.Text(),
            server_default=sa.text("'proposed'"),
            nullable=False,
        ),
        # Autogenerate stripped inner SQL quotes; corrected to sa.text("'medium'").
        sa.Column(
            "current_confidence",
            sa.Text(),
            server_default=sa.text("'medium'"),
            nullable=False,
        ),
        # Autogenerate stripped inner SQL quotes; corrected to sa.text("'steady'").
        sa.Column(
            "current_momentum",
            sa.Text(),
            server_default=sa.text("'steady'"),
            nullable=False,
        ),
        sa.Column("progress_last_changed_at", sa.DateTime(), nullable=True),
        sa.Column("confidence_last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("momentum_last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("confidence_inferred", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("momentum_inferred", sa.Boolean(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "(layer = 'arena' AND engagement_id IS NULL)"
            " OR (layer = 'engagement' AND engagement_id IS NOT NULL)",
            name="ck_hypotheses_layer_engagement",
        ),
        sa.CheckConstraint(
            "current_confidence IN ('low', 'medium', 'high')",
            name="ck_hypotheses_confidence",
        ),
        sa.CheckConstraint(
            "current_momentum IN ('accelerating', 'steady', 'slowing', 'stalled')",
            name="ck_hypotheses_momentum",
        ),
        sa.CheckConstraint(
            "current_progress IN ('proposed', 'in_delivery', 'realised', 'confirmed', 'dead')",
            name="ck_hypotheses_progress",
        ),
        sa.CheckConstraint("layer IN ('arena', 'engagement')", name="ck_hypotheses_layer"),
        sa.ForeignKeyConstraint(
            ["arena_id"],
            ["arenas.id"],
        ),
        sa.ForeignKeyConstraint(
            ["domain"],
            ["domains.id"],
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"],
            ["engagements.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("hypotheses", schema=None) as batch_op:
        batch_op.create_index(
            "idx_hypotheses_arena", ["arena_id", "layer", "closed_at"], unique=False
        )
        batch_op.create_index("idx_hypotheses_domain", ["domain", "layer"], unique=False)
        batch_op.create_index(
            "idx_hypotheses_engagement", ["engagement_id", "closed_at"], unique=False
        )

    op.create_table(
        "atom_attachments",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=26), nullable=False),
        sa.Column(
            "attached_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("attached_by", sa.Text(), nullable=False),
        sa.Column("ambiguity_flag", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("dismissed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("dismissal_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "attached_by IN ('cron_suggested', 'human_confirmed')",
            name="ck_attach_attached_by",
        ),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["hypotheses.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("atom_id", "hypothesis_id", name="uq_atom_attachments"),
    )
    with op.batch_alter_table("atom_attachments", schema=None) as batch_op:
        batch_op.create_index("idx_attach_atom", ["atom_id", "dismissed"], unique=False)
        # Skipped by autogenerate (expression-based index with DESC column).
        batch_op.create_index(
            "idx_attach_hypothesis",
            ["hypothesis_id", "dismissed", sa.text("attached_at DESC")],
            unique=False,
        )

    op.create_table(
        "hypothesis_state_changes",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=26), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "changed_by IN ('cron_inferred', 'human_confirmed', 'human_overridden')",
            name="ck_hsc_changed_by",
        ),
        sa.CheckConstraint(
            "dimension IN ('progress', 'confidence', 'momentum')", name="ck_hsc_dimension"
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["hypotheses.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Skipped by autogenerate (expression-based index with DESC column).
    op.create_index(
        "idx_hsc_hypothesis",
        "hypothesis_state_changes",
        ["hypothesis_id", "dimension", sa.text("changed_at DESC")],
    )

    op.create_table(
        "state_change_evidence",
        sa.Column("state_change_id", sa.String(length=26), nullable=False),
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.ForeignKeyConstraint(
            ["atom_id"],
            ["atoms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["state_change_id"],
            ["hypothesis_state_changes.id"],
        ),
        sa.PrimaryKeyConstraint("state_change_id", "atom_id"),
    )


def downgrade() -> None:
    op.drop_table("state_change_evidence")
    op.drop_index("idx_hsc_hypothesis", table_name="hypothesis_state_changes")
    op.drop_table("hypothesis_state_changes")
    with op.batch_alter_table("atom_attachments", schema=None) as batch_op:
        batch_op.drop_index("idx_attach_hypothesis")
        batch_op.drop_index("idx_attach_atom")
    op.drop_table("atom_attachments")
    with op.batch_alter_table("hypotheses", schema=None) as batch_op:
        batch_op.drop_index("idx_hypotheses_engagement")
        batch_op.drop_index("idx_hypotheses_domain")
        batch_op.drop_index("idx_hypotheses_arena")
    op.drop_table("hypotheses")
    op.drop_index("idx_atom_status_atom", table_name="atom_status_changes")
    op.drop_table("atom_status_changes")
    with op.batch_alter_table("atom_risk_details", schema=None) as batch_op:
        batch_op.drop_index("idx_risk_severity")
    op.drop_table("atom_risk_details")
    op.drop_table("atom_external_refs")
    with op.batch_alter_table("atom_commitment_details", schema=None) as batch_op:
        batch_op.drop_index("idx_commit_status")
        batch_op.drop_index("idx_commit_owner")
        batch_op.drop_index(
            "idx_commit_due",
            sqlite_where=sa.text("current_status NOT IN ('met', 'cancelled')"),
        )
    op.drop_table("atom_commitment_details")
    with op.batch_alter_table("atom_ask_details", schema=None) as batch_op:
        batch_op.drop_index("idx_ask_status")
        batch_op.drop_index("idx_ask_owner")
    op.drop_table("atom_ask_details")
    with op.batch_alter_table("engagements", schema=None) as batch_op:
        batch_op.drop_index("idx_engagements_domain")
        batch_op.drop_index("idx_engagements_arena")
    op.drop_table("engagements")
    with op.batch_alter_table("atoms", schema=None) as batch_op:
        batch_op.drop_index("idx_atoms_type")
        batch_op.drop_index("idx_atoms_dismissed")
        batch_op.drop_index("idx_atoms_event")
        batch_op.drop_index("idx_atoms_artifact")
        batch_op.drop_index("idx_atoms_anchor_event", sqlite_where=sa.text("event_id IS NOT NULL"))
        batch_op.drop_index(
            "idx_atoms_anchor_artifact",
            sqlite_where=sa.text("artifact_id IS NOT NULL"),
        )
    op.drop_table("atoms")
    op.drop_index("idx_av_artifact", table_name="artifact_versions")
    op.drop_table("artifact_versions")
    op.drop_index("idx_events_type", table_name="events")
    op.drop_index("idx_events_domain", table_name="events")
    op.drop_table("events")
    op.drop_index("idx_artifacts_domain", table_name="artifacts")
    op.drop_table("artifacts")
    with op.batch_alter_table("arenas", schema=None) as batch_op:
        batch_op.drop_index("idx_arenas_domain")
    op.drop_table("arenas")
    op.drop_index("idx_triage_pending", table_name="triage_items")
    with op.batch_alter_table("triage_items", schema=None) as batch_op:
        batch_op.drop_index("idx_triage_entity")
    op.drop_table("triage_items")
    op.drop_index("idx_stakeholders_name", table_name="stakeholders")
    with op.batch_alter_table("stakeholders", schema=None) as batch_op:
        batch_op.drop_index("idx_stakeholders_email")
    op.drop_table("stakeholders")
    op.drop_index("idx_proc_runs", table_name="processor_runs")
    op.drop_table("processor_runs")
    with op.batch_alter_table("external_references", schema=None) as batch_op:
        batch_op.drop_index("idx_extref_unreachable")
    op.drop_table("external_references")
    op.drop_table("domains")
    op.drop_index("idx_briefs_scope", table_name="brief_runs")
    op.drop_table("brief_runs")
