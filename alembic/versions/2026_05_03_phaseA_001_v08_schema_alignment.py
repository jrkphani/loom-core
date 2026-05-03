"""v0.8 consolidated schema migration — Phase A, issue #076.

Pre-migration backup ritual (run before applying to production DB):

    LOOM_DB="$HOME/Library/Application Support/Loom"
    cp "$LOOM_DB/loom.db" "$LOOM_DB/loom.db.pre-v08-$(date +%s)"

    # Run on the copy first
    cd /Users/jrkphani/Projects/loom/loom-core
    LOOM_TEST="$LOOM_DB/loom.db.pre-v08-test"
    DATABASE_URL="sqlite+aiosqlite:///$LOOM_TEST" uv run alembic upgrade head

    # Verify
    uv run python -m loom_core.cli doctor
    uv run alembic check
    uv run pytest -m visibility
    uv run pytest

    # If all pass, apply to production
    DATABASE_URL="sqlite+aiosqlite:///$LOOM_DB/loom.db" uv run alembic upgrade head

    # Verify production
    uv run python -m loom_core.cli doctor

The backup file is retained for at least 30 days. Forward-only migration is
the production discipline; rollback is "restore from backup", not
"alembic downgrade".

SPEC CORRECTION — work_stakeholder_roles drop (§1.5):
    The refactor plan §1.5 uses op.drop_index() / op.drop_table() directly.
    Overridden here to use op.execute("DROP INDEX/TABLE IF EXISTS ...") because
    work_stakeholder_roles exists only in loom-meta/docs/loom-schema-v1.sql (a
    spec document) and was never built by the W1/W2 Alembic migrations. The IF
    EXISTS form is safe against any dev DB state and idempotent if the migration
    is ever rerun against a fresh database.

Revision ID: c3a5f71d9e82
Revises: 2026_04_26_b3036cdd7161
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "c3a5f71d9e82"
down_revision: str = "b3036cdd7161"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    """Apply all v0.8 schema additions in section order 1.1 through 1.9.

    Each section is a discrete audit unit. Sections are not collapsed even
    when they touch the same table multiple times (atoms is touched 5x).
    """
    # §1.1 — visibility_scope on 6 tables + entity_visibility_members link table
    _upgrade_1_1_visibility()

    # §1.2 — retention_tier on 7 tables
    # §1.3 — projection_at_creation on 6 tables
    _upgrade_1_2_retention()
    _upgrade_1_3_projection()

    # §1.4 — model-version metadata on atoms, hypothesis_state_changes, brief_runs
    _upgrade_1_4_inference_metadata()

    # §1.5 — stakeholder_roles + drop work_stakeholder_roles IF EXISTS
    _upgrade_1_5_stakeholder_roles()

    # §1.6 — audience profile columns on stakeholders
    _upgrade_1_6_audience_profile()

    # §1.7 — atom_contributions (forward provenance) + atom retraction columns
    _upgrade_1_7_forward_provenance()

    # §1.8 — resources + resource_attributions + asset_uses
    _upgrade_1_8_resources()

    # §1.9 / pre-existing gap — processor_runs.success column (folded from #009)
    _upgrade_1_9_processor_runs_success()


def downgrade() -> None:
    """Reverse all v0.8 additions in reverse section order."""
    _downgrade_1_9_processor_runs_success()
    _downgrade_1_8_resources()
    _downgrade_1_7_forward_provenance()
    _downgrade_1_6_audience_profile()
    _downgrade_1_5_stakeholder_roles()
    _downgrade_1_4_inference_metadata()
    _downgrade_1_3_projection()
    _downgrade_1_2_retention()
    _downgrade_1_1_visibility()


# ---------------------------------------------------------------------------
# §1.1 — Visibility scope
# ---------------------------------------------------------------------------


_VISIBILITY_TABLES = (
    "events",
    "atoms",
    "hypotheses",
    "artifacts",
    "artifact_versions",
    "external_references",
)

_VISIBILITY_CHECK = (
    "visibility_scope IN ('domain_wide', 'engagement_scoped', 'stakeholder_set', 'private')"
)


def _upgrade_1_1_visibility() -> None:
    for table in _VISIBILITY_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "visibility_scope",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'private'"),
                )
            )
            batch_op.create_check_constraint(
                f"ck_{table}_visibility_scope",
                _VISIBILITY_CHECK,
            )

    op.create_table(
        "entity_visibility_members",
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.String(length=26), nullable=False),
        sa.Column(
            "stakeholder_id",
            sa.String(length=26),
            sa.ForeignKey("stakeholders.id"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("entity_type", "entity_id", "stakeholder_id"),
        sa.CheckConstraint(
            "entity_type IN ('event', 'atom', 'hypothesis',"
            " 'artifact', 'artifact_version', 'external_reference')",
            name="ck_evm_entity_type",
        ),
    )
    op.create_index("idx_evm_lookup", "entity_visibility_members", ["entity_type", "entity_id"])


def _downgrade_1_1_visibility() -> None:
    op.drop_index("idx_evm_lookup", table_name="entity_visibility_members")
    op.drop_table("entity_visibility_members")
    for table in reversed(_VISIBILITY_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f"ck_{table}_visibility_scope", type_="check")
            batch_op.drop_column("visibility_scope")


# ---------------------------------------------------------------------------
# §1.2 — Retention tier
# ---------------------------------------------------------------------------


_RETENTION_TABLES = (
    "events",
    "atoms",
    "hypotheses",
    "artifacts",
    "artifact_versions",
    "external_references",
    "engagements",
)

_RETENTION_CHECK = "retention_tier IN ('operational', 'archive_soon', 'archived', 'purge_eligible')"

_PROJECTION_TABLES = (
    "events",
    "atoms",
    "hypotheses",
    "artifacts",
    "engagements",
    "arenas",
)


def _upgrade_1_2_retention() -> None:
    for table in _RETENTION_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "retention_tier",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'operational'"),
                )
            )
            batch_op.create_check_constraint(
                f"ck_{table}_retention_tier",
                _RETENTION_CHECK,
            )
            batch_op.create_index(f"idx_{table}_retention", ["retention_tier"])


def _downgrade_1_2_retention() -> None:
    for table in reversed(_RETENTION_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(f"idx_{table}_retention")
            batch_op.drop_constraint(f"ck_{table}_retention_tier", type_="check")
            batch_op.drop_column("retention_tier")


# ---------------------------------------------------------------------------
# §1.3 — Projection-at-creation
# ---------------------------------------------------------------------------


def _upgrade_1_3_projection() -> None:
    for table in _PROJECTION_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "projection_at_creation",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'work-cro-1cloudhub-v1'"),
                )
            )


def _downgrade_1_3_projection() -> None:
    for table in reversed(_PROJECTION_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("projection_at_creation")


# ---------------------------------------------------------------------------
# §1.4 — Inference metadata
# ---------------------------------------------------------------------------


def _upgrade_1_4_inference_metadata() -> None:
    with op.batch_alter_table("atoms", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extractor_provider", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("extractor_model_version", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("extractor_skill_version", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("extraction_confidence", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("source_span_start", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_span_end", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_atoms_extractor_provider",
            "extractor_provider IS NULL OR extractor_provider IN"
            " ('python_rules', 'embeddings', 'apple_fm', 'claude_api', 'human')",
        )

    with op.batch_alter_table("hypothesis_state_changes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("inference_provider", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("inference_model_version", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("inference_skill_version", sa.Text(), nullable=True))

    with op.batch_alter_table("brief_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("composer_skill_version", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("provider_chain", sa.JSON(), nullable=True))


def _downgrade_1_4_inference_metadata() -> None:
    with op.batch_alter_table("brief_runs", schema=None) as batch_op:
        batch_op.drop_column("provider_chain")
        batch_op.drop_column("composer_skill_version")

    with op.batch_alter_table("hypothesis_state_changes", schema=None) as batch_op:
        batch_op.drop_column("inference_skill_version")
        batch_op.drop_column("inference_model_version")
        batch_op.drop_column("inference_provider")

    with op.batch_alter_table("atoms", schema=None) as batch_op:
        batch_op.drop_constraint("ck_atoms_extractor_provider", type_="check")
        batch_op.drop_column("source_span_end")
        batch_op.drop_column("source_span_start")
        batch_op.drop_column("extraction_confidence")
        batch_op.drop_column("extractor_skill_version")
        batch_op.drop_column("extractor_model_version")
        batch_op.drop_column("extractor_provider")


# ---------------------------------------------------------------------------
# §1.5 — Stakeholder roles
# ---------------------------------------------------------------------------


def _upgrade_1_5_stakeholder_roles() -> None:
    op.create_table(
        "stakeholder_roles",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "stakeholder_id",
            sa.String(length=26),
            sa.ForeignKey("stakeholders.id"),
            nullable=False,
        ),
        sa.Column(
            "domain",
            sa.String(length=26),
            sa.ForeignKey("domains.id"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.String(length=26), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("ended_at", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope_type IN ('arena', 'engagement', 'domain')",
            name="ck_sr_scope_type",
        ),
        sa.CheckConstraint(
            "role IN ('sponsor', 'beneficiary', 'blocker', 'validator',"
            " 'advocate', 'doer', 'influencer', 'advisor',"
            " 'decision_maker', 'informed_party')",
            name="ck_sr_role",
        ),
    )
    op.create_index(
        "idx_sr_current",
        "stakeholder_roles",
        ["stakeholder_id", "scope_id"],
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "idx_sr_scope",
        "stakeholder_roles",
        ["scope_type", "scope_id", "ended_at"],
    )
    # OVERRIDE: refactor plan §1.5 uses op.drop_index/op.drop_table directly.
    # Using IF EXISTS form because work_stakeholder_roles was never built by any
    # Alembic migration — it exists only in loom-meta/docs/loom-schema-v1.sql.
    op.execute(text("DROP INDEX IF EXISTS idx_wsr_stakeholder"))
    op.execute(text("DROP INDEX IF EXISTS idx_wsr_scope"))
    op.execute(text("DROP TABLE IF EXISTS work_stakeholder_roles"))


def _downgrade_1_5_stakeholder_roles() -> None:
    op.drop_index("idx_sr_scope", table_name="stakeholder_roles")
    op.drop_index("idx_sr_current", table_name="stakeholder_roles")
    op.drop_table("stakeholder_roles")


# ---------------------------------------------------------------------------
# §1.6 — Audience profile
# ---------------------------------------------------------------------------


def _upgrade_1_6_audience_profile() -> None:
    with op.batch_alter_table("stakeholders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("audience_schema", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("preferred_depth", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("preferred_channel", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("tone_notes", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_stakeholders_audience_schema",
            "audience_schema IS NULL OR audience_schema IN"
            " ('executive', 'technical', 'aws_partner', 'customer_sponsor', 'visual')",
        )


def _downgrade_1_6_audience_profile() -> None:
    with op.batch_alter_table("stakeholders", schema=None) as batch_op:
        batch_op.drop_constraint("ck_stakeholders_audience_schema", type_="check")
        batch_op.drop_column("tone_notes")
        batch_op.drop_column("preferred_channel")
        batch_op.drop_column("preferred_depth")
        batch_op.drop_column("audience_schema")


# ---------------------------------------------------------------------------
# §1.7 — Forward provenance + retraction
# ---------------------------------------------------------------------------


def _upgrade_1_7_forward_provenance() -> None:
    op.create_table(
        "atom_contributions",
        sa.Column(
            "atom_id",
            sa.String(length=26),
            sa.ForeignKey("atoms.id"),
            nullable=False,
        ),
        sa.Column("consumer_type", sa.Text(), nullable=False),
        sa.Column("consumer_id", sa.String(length=26), nullable=False),
        sa.Column(
            "contributed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("atom_id", "consumer_type", "consumer_id"),
        sa.CheckConstraint(
            "consumer_type IN ('brief_run', 'state_change',"
            " 'draft', 'sent_action', 'derived_atom')",
            name="ck_ac_consumer_type",
        ),
    )
    op.create_index("idx_ac_consumer", "atom_contributions", ["consumer_type", "consumer_id"])

    with op.batch_alter_table("atoms", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("retracted", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("retracted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("retraction_reason", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_atoms_retraction_reason",
            "retraction_reason IS NULL OR retraction_reason IN"
            " ('hallucination', 'wrong_extraction', 'stale_source',"
            " 'corrected_on_review')",
        )
        batch_op.create_index(
            "idx_atoms_retracted",
            ["retracted"],
            sqlite_where=sa.text("retracted = 1"),
        )


def _downgrade_1_7_forward_provenance() -> None:
    with op.batch_alter_table("atoms", schema=None) as batch_op:
        batch_op.drop_index("idx_atoms_retracted")
        batch_op.drop_constraint("ck_atoms_retraction_reason", type_="check")
        batch_op.drop_column("retraction_reason")
        batch_op.drop_column("retracted_at")
        batch_op.drop_column("retracted")
    op.drop_index("idx_ac_consumer", table_name="atom_contributions")
    op.drop_table("atom_contributions")


# ---------------------------------------------------------------------------
# §1.8 — Resources
# ---------------------------------------------------------------------------


def _upgrade_1_8_resources() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("domain", sa.String(length=26), sa.ForeignKey("domains.id"), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("quantity_unit", sa.Text(), nullable=True),
        sa.Column("quality_dimensions", sa.JSON(), nullable=True),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("expiry_at", sa.Date(), nullable=True),
        sa.Column("replenishment_rule", sa.Text(), nullable=True),
        sa.Column("inferred_from", sa.Text(), nullable=True),
        sa.Column(
            "visibility_scope",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'private'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('time', 'people', 'financial', 'attention',"
            " 'credibility', 'knowledge_asset', 'tooling_asset')",
            name="ck_resources_category",
        ),
        sa.CheckConstraint(
            "inferred_from IS NULL OR inferred_from IN"
            " ('calendar_density', 'mailbox_traffic', 'expense_reports',"
            " 'response_patterns', 'usage_logs', 'manual')",
            name="ck_resources_inferred_from",
        ),
    )
    op.create_index("idx_resources_category", "resources", ["domain", "category"])
    op.create_index(
        "idx_resources_expiry",
        "resources",
        ["expiry_at"],
        sqlite_where=sa.text("expiry_at IS NOT NULL"),
    )

    op.create_table(
        "resource_attributions",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "resource_id",
            sa.String(length=26),
            sa.ForeignKey("resources.id"),
            nullable=False,
        ),
        sa.Column("consumer_type", sa.Text(), nullable=False),
        sa.Column("consumer_id", sa.String(length=26), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column(
            "attributed_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "consumer_type IN ('hypothesis', 'engagement', 'draft', 'sent_action')",
            name="ck_ra_consumer_type",
        ),
    )
    op.create_index("idx_ra_resource", "resource_attributions", ["resource_id", "released_at"])
    op.create_index("idx_ra_consumer", "resource_attributions", ["consumer_type", "consumer_id"])

    op.create_table(
        "asset_uses",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "resource_id",
            sa.String(length=26),
            sa.ForeignKey("resources.id"),
            nullable=False,
        ),
        sa.Column("audience_type", sa.Text(), nullable=False),
        sa.Column("used_in_consumer_type", sa.Text(), nullable=False),
        sa.Column("used_in_consumer_id", sa.String(length=26), nullable=False),
        sa.Column(
            "used_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )
    op.create_index("idx_asset_uses", "asset_uses", ["resource_id", "audience_type"])


def _downgrade_1_8_resources() -> None:
    op.drop_index("idx_asset_uses", table_name="asset_uses")
    op.drop_table("asset_uses")
    op.drop_index("idx_ra_consumer", table_name="resource_attributions")
    op.drop_index("idx_ra_resource", table_name="resource_attributions")
    op.drop_table("resource_attributions")
    op.drop_index("idx_resources_expiry", table_name="resources")
    op.drop_index("idx_resources_category", table_name="resources")
    op.drop_table("resources")


# ---------------------------------------------------------------------------
# §1.9 — processor_runs.success (pre-existing gap from #009)
# ---------------------------------------------------------------------------


def _upgrade_1_9_processor_runs_success() -> None:
    with op.batch_alter_table("processor_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "success",
                sa.Boolean(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )


def _downgrade_1_9_processor_runs_success() -> None:
    with op.batch_alter_table("processor_runs", schema=None) as batch_op:
        batch_op.drop_column("success")
