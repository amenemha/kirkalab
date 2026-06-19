"""Migration 0005 applies cleanly against a fresh SQLite database.

Runs the real Alembic stack (env.py + all revisions) end-to-end in a subprocess
so it is isolated from the in-memory test engine. Verifies the upgrade creates
the new schema and seeds the full catalog, and that downgrade is reversible.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_SIZE = 184
# Migration 0004 seeds 13 starter ASICs; 6 of them (MicroBT Whatsminer M50..M66)
# have no counterpart in the full catalog, so they survive alongside it.
STARTERS_ONLY = 6
EXPECTED_AFTER_MIGRATION = CATALOG_SIZE + STARTERS_ONLY


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
    return f"sqlite:///{tmp_path / 'migration_test.db'}"


def test_upgrade_head_creates_schema_and_seeds(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)

    tables = set(insp.get_table_names())
    assert "user_settings" in tables
    assert "device_models" in tables

    cols = {c["name"] for c in insp.get_columns("device_models")}
    for expected in (
        "series",
        "variant",
        "efficiency_j_per_th",
        "cooling_type",
        "source_url",
        "notes",
    ):
        assert expected in cols

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM device_models")
        ).scalar_one()
        assert count == EXPECTED_AFTER_MIGRATION
        # Every catalog model is present.
        assert count >= CATALOG_SIZE
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0004_calc_core_asic_data")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    assert "user_settings" not in set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("device_models")}
    assert "variant" not in cols
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
