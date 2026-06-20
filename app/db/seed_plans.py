"""Idempotent seed of billing plans (CALC_SPEC §4 / §5).

Upserts the FREE + PRO tariffs by ``code``. Prices live in the table, never in
code: the bot/API read ``price_stars`` from here. Re-running refreshes the
fields of an existing row in place and never creates duplicates, so it is safe
to run on every deploy or from the Alembic migration that creates the table.

Run standalone with ``python -m app.db.seed_plans`` or let it run as part of
Alembic migration ``0014`` (which passes a live connection).
"""
from __future__ import annotations

import logging

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger("app.seed_plans")

# Default tariffs. Prices are the spec's PROPOSED Stars amounts (§4); the
# customer can tune them by updating the rows. ``period_days`` is the
# entitlement length; the FREE plan is perpetual (None). ``features`` mirror
# CALC_SPEC §5 PRO entitlements so the gate reads them from config.
_PRO_FEATURES = [
    "unlimited_calcs",
    "local_currencies",
    "personal_settings",
    "mixed_farms",
    "model_compare",
    "extended_history",
    "advanced_roi",
    "custom_asic_profiles",
    "pro_scenarios",
]

PLANS: list[dict] = [
    {
        "code": "free",
        "title": "Free",
        "period_days": None,
        "price_stars": 0,
        "currency": "XTR",
        "features_json": [],
        "limits_json": {"intro_calcs": 5, "daily_calcs": 3},
        "is_active": True,
        "sort_order": 0,
    },
    {
        "code": "pro_monthly",
        "title": "PRO на месяц",
        "period_days": 30,
        "price_stars": 250,
        "currency": "XTR",
        "features_json": _PRO_FEATURES,
        "limits_json": {"unlimited_calcs": True},
        "is_active": True,
        "sort_order": 1,
    },
    {
        "code": "pro_yearly",
        "title": "PRO на год",
        "period_days": 365,
        "price_stars": 2500,
        "currency": "XTR",
        "features_json": _PRO_FEATURES,
        "limits_json": {"unlimited_calcs": True},
        "is_active": True,
        "sort_order": 2,
    },
]


def seed_plans(bind: Connection | Engine) -> int:
    """Upsert all default plans. Returns the number of rows inserted/updated."""
    touched = 0
    with Session(bind=bind) as session:
        for spec in PLANS:
            row = session.scalar(
                select(models.Plan).where(models.Plan.code == spec["code"])
            )
            if row is None:
                session.add(models.Plan(**spec))
            else:
                for field, value in spec.items():
                    setattr(row, field, value)
            touched += 1
        session.commit()
    logger.info("Seeded %d billing plans", touched)
    return touched


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import engine

    logging.basicConfig(level=logging.INFO)
    seed_plans(engine)
