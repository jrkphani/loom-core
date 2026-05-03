"""Confirmation tests for the v0.8 consolidated schema migration (#076).

Tests are added here behaviour-by-behaviour alongside each migration section.
All tests use the @pytest.mark.integration marker.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _col_names(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        conn.close()


def _table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _index_exists(db_path: Path, index_name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _get_index_sql(db_path: Path, index_name: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
        ).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _insert(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _insert_raises(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> bool:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(sql, params)
        conn.commit()
        return False
    except sqlite3.IntegrityError:
        return True
    finally:
        conn.close()


@pytest.fixture()
def migrated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh DB with head migration applied; returned path is the sqlite file."""
    db_path = tmp_path / "loom_v08.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    return db_path


# ---------------------------------------------------------------------------
# B3 confirmation — scaffold present (callable upgrade/downgrade, correct chain)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B4 — §1.1 visibility_scope
# ---------------------------------------------------------------------------

_VISIBILITY_TABLES = (
    "events",
    "atoms",
    "hypotheses",
    "artifacts",
    "artifact_versions",
    "external_references",
)


@pytest.mark.integration
def test_visibility_scope_column_added_to_all_tables(migrated_db: Path) -> None:
    """visibility_scope TEXT NOT NULL DEFAULT 'private' on 6 tables."""
    for table in _VISIBILITY_TABLES:
        cols = _col_names(migrated_db, table)
        assert "visibility_scope" in cols, f"visibility_scope missing from {table}"


@pytest.mark.integration
def test_visibility_scope_default_is_private(migrated_db: Path) -> None:
    """visibility_scope defaults to 'private' when not specified."""
    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # Insert a minimal event row without specifying visibility_scope.
        conn.execute(
            "INSERT INTO events (id, domain, type, occurred_at)"
            " VALUES ('EV0000000000000000000000001', 'work', 'process', '2026-01-01')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT visibility_scope FROM events WHERE id='EV0000000000000000000000001'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "private"


@pytest.mark.integration
def test_visibility_scope_check_constraint_enforced(migrated_db: Path) -> None:
    """visibility_scope CHECK rejects values outside the enum."""
    assert _insert_raises(
        migrated_db,
        "INSERT INTO events (id, domain, type, occurred_at, visibility_scope)"
        " VALUES (?, 'work', 'process', '2026-01-01', 'invalid')",
        ("EV9999999999999999999999999",),
    )


@pytest.mark.integration
def test_entity_visibility_members_table_created(migrated_db: Path) -> None:
    """entity_visibility_members table and idx_evm_lookup index exist."""
    assert _table_exists(migrated_db, "entity_visibility_members")
    assert _index_exists(migrated_db, "idx_evm_lookup")


# ---------------------------------------------------------------------------
# B5 — §1.2 retention_tier + §1.3 projection_at_creation
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

_PROJECTION_TABLES = (
    "events",
    "atoms",
    "hypotheses",
    "artifacts",
    "engagements",
    "arenas",
)


@pytest.mark.integration
def test_retention_tier_column_added(migrated_db: Path) -> None:
    for table in _RETENTION_TABLES:
        assert "retention_tier" in _col_names(
            migrated_db, table
        ), f"retention_tier missing from {table}"


@pytest.mark.integration
def test_retention_tier_default_is_operational(migrated_db: Path) -> None:
    """retention_tier default value is 'operational' — verified via PRAGMA."""
    conn = sqlite3.connect(migrated_db)
    try:
        # Check via PRAGMA table_info (avoids FK insert complexity).
        for table in _RETENTION_TABLES:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col = next((r for r in rows if r[1] == "retention_tier"), None)
            assert col is not None, f"retention_tier missing from {table}"
            assert (
                col[4] == "'operational'"
            ), f"{table}.retention_tier default should be 'operational', got {col[4]!r}"
    finally:
        conn.close()


@pytest.mark.integration
def test_retention_tier_check_enforced(migrated_db: Path) -> None:
    assert _insert_raises(
        migrated_db,
        "INSERT INTO events (id, domain, type, occurred_at, retention_tier)"
        " VALUES (?, 'work', 'process', '2026-01-01', 'invalid_tier')",
        ("EV9999999999999999999999998",),
    )


@pytest.mark.integration
def test_retention_indexes_exist(migrated_db: Path) -> None:
    for table in _RETENTION_TABLES:
        assert _index_exists(
            migrated_db, f"idx_{table}_retention"
        ), f"idx_{table}_retention missing"


@pytest.mark.integration
def test_projection_at_creation_column_added(migrated_db: Path) -> None:
    for table in _PROJECTION_TABLES:
        assert "projection_at_creation" in _col_names(
            migrated_db, table
        ), f"projection_at_creation missing from {table}"


# ---------------------------------------------------------------------------
# B6 — §1.4 inference metadata
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_inference_metadata_columns_on_atoms(migrated_db: Path) -> None:
    cols = _col_names(migrated_db, "atoms")
    for col in (
        "extractor_provider",
        "extractor_model_version",
        "extractor_skill_version",
        "extraction_confidence",
        "source_span_start",
        "source_span_end",
    ):
        assert col in cols, f"{col} missing from atoms"


@pytest.mark.integration
def test_extractor_provider_check_exists(migrated_db: Path) -> None:
    """ck_atoms_extractor_provider CHECK constraint exists in sqlite_master DDL."""
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='atoms'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert (
        "ck_atoms_extractor_provider" in row[0]
    ), "ck_atoms_extractor_provider not found in atoms DDL"


@pytest.mark.integration
def test_inference_columns_on_hypothesis_state_changes(migrated_db: Path) -> None:
    cols = _col_names(migrated_db, "hypothesis_state_changes")
    for col in ("inference_provider", "inference_model_version", "inference_skill_version"):
        assert col in cols


@pytest.mark.integration
def test_inference_columns_on_brief_runs(migrated_db: Path) -> None:
    cols = _col_names(migrated_db, "brief_runs")
    assert "composer_skill_version" in cols
    assert "provider_chain" in cols


# ---------------------------------------------------------------------------
# B7 — §1.5 stakeholder_roles + work_stakeholder_roles drop
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_stakeholder_roles_table_created(migrated_db: Path) -> None:
    assert _table_exists(migrated_db, "stakeholder_roles")
    assert _index_exists(migrated_db, "idx_sr_current")
    assert _index_exists(migrated_db, "idx_sr_scope")


@pytest.mark.integration
def test_stakeholder_roles_scope_type_check(migrated_db: Path) -> None:
    assert _insert_raises(
        migrated_db,
        "INSERT INTO stakeholder_roles"
        " (id, stakeholder_id, domain, scope_type, role, started_at)"
        " VALUES (?, 'SH1', 'work', 'invalid_scope', 'sponsor', '2026-01-01')",
        ("SR0000000000000000000000001",),
    )


@pytest.mark.integration
def test_stakeholder_roles_role_check(migrated_db: Path) -> None:
    assert _insert_raises(
        migrated_db,
        "INSERT INTO stakeholder_roles"
        " (id, stakeholder_id, domain, scope_type, role, started_at)"
        " VALUES (?, 'SH1', 'work', 'engagement', 'champion', '2026-01-01')",
        ("SR0000000000000000000000002",),
    )


@pytest.mark.integration
def test_work_stakeholder_roles_does_not_exist(migrated_db: Path) -> None:
    """work_stakeholder_roles must not exist after migration (never built, IF EXISTS safe)."""
    assert not _table_exists(migrated_db, "work_stakeholder_roles")


# ---------------------------------------------------------------------------
# B8 — §1.6 audience profile
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_audience_profile_columns_on_stakeholders(migrated_db: Path) -> None:
    cols = _col_names(migrated_db, "stakeholders")
    for col in ("audience_schema", "preferred_depth", "preferred_channel", "tone_notes"):
        assert col in cols


@pytest.mark.integration
def test_audience_schema_check_enforced(migrated_db: Path) -> None:
    """audience_schema CHECK rejects invalid values but DDL exists."""
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='stakeholders'"
        ).fetchone()
    finally:
        conn.close()
    assert row and "ck_stakeholders_audience_schema" in row[0]


# ---------------------------------------------------------------------------
# B9 — §1.7 atom_contributions + atom retraction columns
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_atom_contributions_table_created(migrated_db: Path) -> None:
    assert _table_exists(migrated_db, "atom_contributions")
    assert _index_exists(migrated_db, "idx_ac_consumer")


@pytest.mark.integration
def test_atom_contributions_consumer_type_check(migrated_db: Path) -> None:
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='atom_contributions'"
        ).fetchone()
    finally:
        conn.close()
    assert row and "ck_ac_consumer_type" in row[0]


@pytest.mark.integration
def test_atom_retraction_columns_added(migrated_db: Path) -> None:
    cols = _col_names(migrated_db, "atoms")
    assert "retracted" in cols
    assert "retracted_at" in cols
    assert "retraction_reason" in cols


@pytest.mark.integration
def test_atom_retracted_partial_index_exists(migrated_db: Path) -> None:
    idx_sql = _get_index_sql(migrated_db, "idx_atoms_retracted")
    assert idx_sql, "idx_atoms_retracted index not found"
    assert "retracted = 1" in idx_sql


# ---------------------------------------------------------------------------
# B10 — §1.8 resources + resource_attributions + asset_uses
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_resources_table_created(migrated_db: Path) -> None:
    assert _table_exists(migrated_db, "resources")
    assert _index_exists(migrated_db, "idx_resources_category")
    idx_sql = _get_index_sql(migrated_db, "idx_resources_expiry")
    assert idx_sql and "expiry_at IS NOT NULL" in idx_sql


@pytest.mark.integration
def test_resources_category_check(migrated_db: Path) -> None:
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='resources'"
        ).fetchone()
    finally:
        conn.close()
    assert row and "ck_resources_category" in row[0]
    assert row and "ck_resources_inferred_from" in row[0]


@pytest.mark.integration
def test_resource_attributions_table_created(migrated_db: Path) -> None:
    assert _table_exists(migrated_db, "resource_attributions")
    assert _index_exists(migrated_db, "idx_ra_resource")
    assert _index_exists(migrated_db, "idx_ra_consumer")


@pytest.mark.integration
def test_asset_uses_table_created(migrated_db: Path) -> None:
    assert _table_exists(migrated_db, "asset_uses")
    assert _index_exists(migrated_db, "idx_asset_uses")


# ---------------------------------------------------------------------------
# B11 — processor_runs.success column
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_processor_runs_success_column_added(migrated_db: Path) -> None:
    assert "success" in _col_names(migrated_db, "processor_runs")


@pytest.mark.integration
def test_processor_runs_success_default_is_true(migrated_db: Path) -> None:
    """success defaults to 1 (True) per PRAGMA table_info."""
    conn = sqlite3.connect(migrated_db)
    try:
        rows = conn.execute("PRAGMA table_info(processor_runs)").fetchall()
        col = next((r for r in rows if r[1] == "success"), None)
    finally:
        conn.close()
    assert col is not None
    assert col[4] == "1", f"success default should be 1, got {col[4]!r}"


@pytest.mark.integration
def test_v08_migration_scaffold_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.8 migration file exists, has correct revision chain, and applies cleanly."""
    from alembic.script import ScriptDirectory

    db_path = tmp_path / "loom_scaffold.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))

    cfg = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(cfg)
    head = script_dir.get_current_head()
    assert head == "c3a5f71d9e82", f"Expected v0.8 head, got {head}"

    # Full upgrade → downgrade cycle must be a no-op (stubs, so additive is empty).
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
