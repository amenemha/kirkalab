"""Migration 0015 (calc_run history snapshot columns) up/down/up on SQLite.

Runs the real Alembic stack in a subprocess, mirroring the other migration
tests. Verifies the two new nullable columns on calculation_runs and a
reversible downgrade to 0014."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

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
    return f"sqlite:///{tmp_path / 'migration_0015.db'}"


def _calc_run_columns(engine):
    insp = inspect(engine)
    return {c["name"] for c in insp.get_columns("calculation_runs")}


def test_upgrade_adds_history_snapshot_columns(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    cols = _calc_run_columns(engine)
    assert "device_name" in cols
    assert "net_profit_month_usdt" in cols
    # Existing columns are preserved.
    assert "net_profit_day_usdt" in cols
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0014_billing_plans_subscriptions")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    cols = _calc_run_columns(engine)
    assert "device_name" not in cols
    assert "net_profit_month_usdt" not in cols
    # The table itself and its original columns survive the downgrade.
    assert "net_profit_day_usdt" in cols
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
