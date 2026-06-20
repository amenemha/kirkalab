"""Migration 0014 (billing plans + subscriptions) up/down/up on SQLite.

Runs the real Alembic stack in a subprocess, mirroring the other migration
tests. Verifies the tables, the users.premium_until column, the unique charge-id
index, the seeded plans, and a reversible downgrade to 0013."""
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
    return f"sqlite:///{tmp_path / 'migration_0014.db'}"


def test_upgrade_creates_billing_tables_and_seeds_plans(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"plans", "subscriptions"} <= tables

    user_cols = {c["name"] for c in insp.get_columns("users")}
    assert "premium_until" in user_cols

    # charge id is uniquely indexed (idempotency key).
    indexes = insp.get_indexes("subscriptions")
    charge_idx = next(
        (
            ix
            for ix in indexes
            if ix["column_names"] == ["telegram_payment_charge_id"]
        ),
        None,
    )
    assert charge_idx is not None and charge_idx["unique"]

    # FKs on subscriptions: users + plans.
    referred = {fk["referred_table"] for fk in insp.get_foreign_keys("subscriptions")}
    assert {"users", "plans"} <= referred

    # Plans were seeded with the spec prices.
    with engine.connect() as conn:
        rows = dict(
            conn.execute(text("SELECT code, price_stars FROM plans")).all()
        )
    assert rows.get("pro_monthly") == 250
    assert rows.get("pro_yearly") == 2500
    assert rows.get("free") == 0
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0013_manual_import_files")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "subscriptions" not in tables
    assert "plans" not in tables
    assert "premium_until" not in {c["name"] for c in insp.get_columns("users")}
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
