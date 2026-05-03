"""Integration test: W1 Alembic migration applies cleanly.

Verifies that:
- upgrade head creates all 21 tables
- the domains seed row is present
- downgrade base removes all tables
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

EXPECTED_TABLES = sorted(
    [
        "arenas",
        "artifact_versions",
        "artifacts",
        "atom_ask_details",
        "atom_attachments",
        "atom_commitment_details",
        "atom_contributions",
        "atom_external_refs",
        "atom_risk_details",
        "atom_status_changes",
        "atoms",
        "asset_uses",
        "brief_runs",
        "domains",
        "engagements",
        "entity_visibility_members",
        "events",
        "external_references",
        "hypotheses",
        "hypothesis_state_changes",
        "processor_runs",
        "resource_attributions",
        "resources",
        "stakeholder_roles",
        "stakeholders",
        "state_change_evidence",
        "triage_items",
        # W2 #002-003: section 4 (work projection)
        "work_account_metadata",
        "work_ask_side",
        "work_commitment_direction",
        "work_engagement_metadata",
    ]
)


def _user_tables(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'"
            " AND name NOT LIKE 'alembic_%'"
            " ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@pytest.mark.integration
def test_migration_upgrade_creates_all_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    assert _user_tables(db_path) == EXPECTED_TABLES


@pytest.mark.integration
def test_migration_domains_seed_row_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT id, display_name, privacy_tier FROM domains").fetchone()
    finally:
        conn.close()

    assert row == ("work", "Work / CRO", "standard")


@pytest.mark.integration
def test_migration_downgrade_removes_all_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    assert _user_tables(db_path) == []
