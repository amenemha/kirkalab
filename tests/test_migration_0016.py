"""Migration 0016 (currency layer: currencies + fx_rates) up/down/up on SQLite.

Runs the real Alembic stack in a subprocess, mirroring the other migration
tests. Verifies the two new tables, the seeded currency catalog, and a
reversible downgrade to 0015."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    return f"sqlite:///{tmp_path / 'migration_0016.db'}"


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_upgrade_creates_tables_and_seeds_currencies(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    tables = _tables(engine)
    assert "currencies" in tables
    assert "fx_rates" in tables

    with engine.connect() as conn:
        codes = {
            r[0] for r in conn.execute(text("SELECT code FROM currencies"))
        }
    assert {"USDT", "USD", "RUB", "KZT", "UAH", "EUR"} <= codes
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0015_calc_run_history_snapshot")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    tables = _tables(engine)
    assert "currencies" not in tables
    assert "fx_rates" not in tables
    # Pre-existing tables survive the downgrade.
    assert "calculation_runs" in tables
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
