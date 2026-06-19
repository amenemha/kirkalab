"""Migration 0006 applies cleanly against a fresh SQLite database.

Runs the real Alembic stack end-to-end in a subprocess, isolated from the
in-memory test engine. Verifies the new tables, the ``users.is_pro`` column, the
seeded firmware presets, and a reversible downgrade.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PRESETS = 24


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
    return f"sqlite:///{tmp_path / 'migration_0006.db'}"


def test_upgrade_head_creates_schema_and_seeds(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)

    tables = set(insp.get_table_names())
    assert "firmware_presets" in tables
    assert "user_firmware_builds" in tables

    user_cols = {c["name"] for c in insp.get_columns("users")}
    assert "is_pro" in user_cols

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM firmware_presets")
        ).scalar_one()
        assert count == EXPECTED_PRESETS
        # Every preset is attached to a real device model.
        orphans = conn.execute(
            text(
                "SELECT COUNT(*) FROM firmware_presets fp "
                "LEFT JOIN device_models dm ON dm.id = fp.device_model_id "
                "WHERE dm.id IS NULL"
            )
        ).scalar_one()
        assert orphans == 0
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0005_device_catalog_full_import")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "firmware_presets" not in tables
    assert "user_firmware_builds" not in tables
    assert "is_pro" not in {c["name"] for c in insp.get_columns("users")}
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
