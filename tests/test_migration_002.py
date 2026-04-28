"""Migration test: W2 #002 adds work_account_metadata table."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _columns(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in rows]
    finally:
        conn.close()


def _fk_list(db_path: Path, table: str) -> list[tuple[str, str, str]]:
    """Return (from_col, to_table, to_col) for each FK on the table."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        return [(r[3], r[2], r[4]) for r in rows]
    finally:
        conn.close()


@pytest.mark.integration
def test_w2_002_migration_adds_work_account_metadata(
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
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                " AND name NOT LIKE 'alembic_%'"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert "work_account_metadata" in tables

    cols = _columns(db_path, "work_account_metadata")
    assert "arena_id" in cols
    assert "industry" in cols
    assert "region" in cols
    assert "aws_segment" in cols
    assert "customer_type" in cols

    fks = _fk_list(db_path, "work_account_metadata")
    assert any(
        from_col == "arena_id" and to_table == "arenas" for from_col, to_table, _ in fks
    ), f"Expected FK arena_id → arenas, got: {fks}"
