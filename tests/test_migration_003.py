"""Migration test: W2 #003 adds work_engagement_metadata, work_commitment_direction,
and work_ask_side tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _columns(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        conn.close()


def _fk_list(db_path: Path, table: str) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            (r[3], r[2], r[4]) for r in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        ]
    finally:
        conn.close()


def _table_sql(db_path: Path, table: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


@pytest.mark.integration
def test_w2_003_migration_adds_work_engagement_metadata_and_atom_extension_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "loom.sqlite"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[core]\ndb_path = "{db_path}"\n')
    monkeypatch.setenv("LOOM_CONFIG_PATH", str(config_path))

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    # ---- work_engagement_metadata ----
    wem_cols = _columns(db_path, "work_engagement_metadata")
    assert "engagement_id" in wem_cols
    assert "sow_value" in wem_cols
    assert "sow_currency" in wem_cols
    assert "aws_funded" in wem_cols
    assert "aws_program" in wem_cols
    assert "swim_lane" in wem_cols

    wem_fks = _fk_list(db_path, "work_engagement_metadata")
    assert any(
        from_col == "engagement_id" and to_table == "engagements"
        for from_col, to_table, _ in wem_fks
    ), f"Expected FK engagement_id → engagements, got: {wem_fks}"

    # swim_lane CHECK constraint via invalid insert
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO work_engagement_metadata (engagement_id, swim_lane)"
                " VALUES ('00000000000000000000000001', 'invalid_lane')"
            )
    finally:
        conn.close()

    # aws_funded NOT NULL — verified via column info
    conn2 = sqlite3.connect(db_path)
    try:
        info = {
            r[1]: r for r in conn2.execute("PRAGMA table_info(work_engagement_metadata)").fetchall()
        }
    finally:
        conn2.close()
    assert info["aws_funded"][3] == 1, "aws_funded must be NOT NULL"

    # ---- work_commitment_direction ----
    wcd_cols = _columns(db_path, "work_commitment_direction")
    assert "atom_id" in wcd_cols
    assert "direction" in wcd_cols

    wcd_fks = _fk_list(db_path, "work_commitment_direction")
    assert any(
        from_col == "atom_id" and to_table == "atoms" for from_col, to_table, _ in wcd_fks
    ), f"Expected FK atom_id → atoms on work_commitment_direction, got: {wcd_fks}"

    # ---- work_ask_side ----
    was_cols = _columns(db_path, "work_ask_side")
    assert "atom_id" in was_cols
    assert "side" in was_cols

    was_fks = _fk_list(db_path, "work_ask_side")
    assert any(
        from_col == "atom_id" and to_table == "atoms" for from_col, to_table, _ in was_fks
    ), f"Expected FK atom_id → atoms on work_ask_side, got: {was_fks}"
