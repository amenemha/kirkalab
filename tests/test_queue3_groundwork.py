"""Queue 3 groundwork: pool / wallet / tax schema.

Covers the ORM models (insert/select, defaults, FK wiring) and the Alembic
migration (clean upgrade + reversible downgrade) for the neutral database
groundwork. There is no business logic to exercise -- only the schema.
"""
import datetime
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.db import models

REPO_ROOT = Path(__file__).resolve().parent.parent

QUEUE3_TABLES = [
    "pool_connections",
    "pool_earnings",
    "wallet_sources",
    "wallet_earnings",
    "tax_rates",
    "tax_reports",
    "tax_deductions",
]


def _make_user(db, handle="q3_user"):
    user = models.User(
        email=f"{handle}@example.com",
        handle=handle,
        hashed_password="x",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# --------------------------------------------------------------------------
# ORM model behaviour
# --------------------------------------------------------------------------


def test_pool_connection_and_earning(db):
    user = _make_user(db, handle="q3_pool")
    conn = models.PoolConnection(user_id=user.id, pool_code="viabtc")
    db.add(conn)
    db.commit()
    db.refresh(conn)

    # is_active defaults to True; created_at is populated.
    assert conn.is_active is True
    assert conn.created_at is not None
    assert conn.observer_url is None

    earning = models.PoolEarning(
        pool_connection_id=conn.id,
        date=datetime.date(2026, 6, 1),
        coin="BTC",
        amount_crypto=Decimal("0.000123450000"),
        raw_json={"gross": "0.00012345"},
    )
    db.add(earning)
    db.commit()

    loaded = (
        db.query(models.PoolEarning)
        .filter_by(pool_connection_id=conn.id)
        .one()
    )
    assert loaded.source == "pool"  # server default
    assert loaded.amount_crypto == Decimal("0.000123450000")
    assert loaded.raw_json == {"gross": "0.00012345"}


def test_wallet_source_and_earning(db):
    user = _make_user(db, handle="q3_wallet")
    wallet = models.WalletSource(user_id=user.id, chain="BTC", address="bc1qx")
    db.add(wallet)
    db.commit()
    db.refresh(wallet)

    assert wallet.is_active is True
    assert wallet.label is None

    tx = models.WalletEarning(
        wallet_source_id=wallet.id,
        tx_hash="deadbeef",
        coin="BTC",
        amount_crypto=Decimal("0.5"),
        raw_json={"confirmations": 6},
    )
    db.add(tx)
    db.commit()

    loaded = (
        db.query(models.WalletEarning)
        .filter_by(wallet_source_id=wallet.id)
        .one()
    )
    assert loaded.tx_hash == "deadbeef"
    assert loaded.raw_json == {"confirmations": 6}
    assert loaded.date is None


def test_tax_rate_insert(db):
    rate = models.TaxRate(
        date=datetime.date(2026, 6, 1),
        coin="BTC",
        currency="RUB",
        rate=Decimal("5500000.5"),
        source="cbr",
    )
    db.add(rate)
    db.commit()

    loaded = db.query(models.TaxRate).one()
    assert loaded.rate == Decimal("5500000.5")
    assert loaded.source == "cbr"


def test_tax_report_and_deduction(db):
    user = _make_user(db, handle="q3_tax")
    report = models.TaxReport(user_id=user.id, period_type="year")
    db.add(report)
    db.commit()
    db.refresh(report)

    # jurisdiction defaults to RU.
    assert report.jurisdiction == "RU"
    assert report.status is None
    assert report.created_at is not None

    deduction = models.TaxDeduction(
        user_id=user.id,
        tax_report_id=report.id,
        type="electricity",
        amount=Decimal("1234.56"),
        currency="RUB",
    )
    db.add(deduction)
    db.commit()

    loaded = db.query(models.TaxDeduction).one()
    assert loaded.type == "electricity"
    assert loaded.amount == Decimal("1234.56")
    assert loaded.tax_report_id == report.id


def test_tax_deduction_report_nullable(db):
    """tax_report_id is optional -- a standalone deduction is allowed."""
    user = _make_user(db, handle="q3_dedonly")
    deduction = models.TaxDeduction(user_id=user.id, type="rent")
    db.add(deduction)
    db.commit()

    loaded = db.query(models.TaxDeduction).one()
    assert loaded.tax_report_id is None


# --------------------------------------------------------------------------
# Alembic migration (real stack, isolated SQLite database)
# --------------------------------------------------------------------------


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
    return f"sqlite:///{tmp_path / 'migration_queue3.db'}"


def test_upgrade_head_creates_queue3_schema(sqlite_url):
    result = _alembic(sqlite_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table in QUEUE3_TABLES:
        assert table in tables, table
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(sqlite_url):
    up = _alembic(sqlite_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(sqlite_url, "downgrade", "0006_firmware_presets_builds")
    assert down.returncode == 0, down.stderr

    engine = create_engine(sqlite_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table in QUEUE3_TABLES:
        assert table not in tables, table
    engine.dispose()

    up_again = _alembic(sqlite_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr
