"""Idempotent seed of the supported currencies (Queue 3 currency layer).

Upserts the fixed currency catalog by ``code``. USDT is the anchor/base every
fx rate is expressed against; the fiats are the local display currencies the
calc result can be converted into. Re-running refreshes an existing row in place
and never creates duplicates, so it is safe to run on every deploy or from the
Alembic migration that creates the table.

Run standalone with ``python -m app.db.seed_currencies`` or let it run as part
of Alembic migration ``0016`` (which passes a live connection). Mirrors the
``seed_plans`` pattern (CALC_SPEC §4) so the data lives in the table, not code.
"""
from __future__ import annotations

import logging

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger("app.seed_currencies")

# USDT first as the anchor (is_fiat=False); the rest are local fiat currencies.
# ``decimals`` is the display/rounding scale: 2 for the fiats here, 2 for the
# USDT anchor (the calc surfaces USDT to 2 places in the bot, the raw 8-place
# figure stays in net_profit_*_usdt). ``sort_order`` drives the bot's picker.
CURRENCIES: list[dict] = [
    {
        "code": "USDT",
        "symbol": "₮",
        "name": "Tether USD",
        "decimals": 2,
        "is_fiat": False,
        "is_active": True,
        "sort_order": 0,
    },
    {
        "code": "USD",
        "symbol": "$",
        "name": "US Dollar",
        "decimals": 2,
        "is_fiat": True,
        "is_active": True,
        "sort_order": 1,
    },
    {
        "code": "RUB",
        "symbol": "₽",
        "name": "Russian Ruble",
        "decimals": 2,
        "is_fiat": True,
        "is_active": True,
        "sort_order": 2,
    },
    {
        "code": "KZT",
        "symbol": "₸",
        "name": "Kazakhstani Tenge",
        "decimals": 2,
        "is_fiat": True,
        "is_active": True,
        "sort_order": 3,
    },
    {
        "code": "UAH",
        "symbol": "₴",
        "name": "Ukrainian Hryvnia",
        "decimals": 2,
        "is_fiat": True,
        "is_active": True,
        "sort_order": 4,
    },
    {
        "code": "EUR",
        "symbol": "€",
        "name": "Euro",
        "decimals": 2,
        "is_fiat": True,
        "is_active": True,
        "sort_order": 5,
    },
]


def seed_currencies(bind: Connection | Engine) -> int:
    """Upsert all supported currencies. Returns the number touched."""
    touched = 0
    with Session(bind=bind) as session:
        for spec in CURRENCIES:
            row = session.scalar(
                select(models.Currency).where(models.Currency.code == spec["code"])
            )
            if row is None:
                session.add(models.Currency(**spec))
            else:
                for field, value in spec.items():
                    setattr(row, field, value)
            touched += 1
        session.commit()
    logger.info("Seeded %d currencies", touched)
    return touched


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import engine

    logging.basicConfig(level=logging.INFO)
    seed_currencies(engine)
