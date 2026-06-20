"""Migration 0013 (manual_import_files) applies and reverses on SQLite.

Runs the real Alembic stack end-to-end in a subprocess, mirroring the other
migration tests. Verifies the new table, its columns, the user FK, and a
reversible downgrade to 0012.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "file_path",
    "original_filename",
    "status",
    "rows_parsed",
    "error_log",
    "created_at",
}


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env["ENVIRONMENT"] = "test"
    env.setdefault("BOT_INTERNAL_SECRET", "test-bot-secret")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'migration_0013.db'}"


def test_upgrade_head_creates_manual_import_files(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    assert "manual_import_files" in set(insp.get_table_names())

    cols = {c["name"] for c in insp.get_columns("manual_import_files")}
    assert EXPECTED_COLUMNS <= cols

    fks = insp.get_foreign_keys("manual_import_files")
    assert any(fk["referred_table"] == "users" for fk in fks)
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0012_calculation_runs")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    assert "manual_import_files" not in set(insp.get_table_names())
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
