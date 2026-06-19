"""Idempotent seed of the starter BTC ASIC catalog.

Run standalone with ``python -m app.db.seed_asic`` or let it run automatically
as part of Alembic migration ``0003`` (which passes a live connection).

Values are real, approximate factory specs and are flagged
``data_quality='factory'``. The seed upserts by ``(brand, model_name)`` so it
never creates duplicates on repeated runs.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import models

# (brand, model_name, hashrate_ths, power_w)
STARTER_ASICS: list[tuple[str, str, str, int]] = [
    ("Bitmain", "Antminer S19", "95", 3250),
    ("Bitmain", "Antminer S19 Pro", "110", 3250),
    ("Bitmain", "Antminer S19 XP", "140", 3010),
    ("Bitmain", "Antminer S21", "200", 3500),
    ("Bitmain", "Antminer S21 Pro", "234", 3531),
    ("Bitmain", "Antminer S21 XP", "270", 3645),
    ("Bitmain", "Antminer T21", "190", 3610),
    ("MicroBT", "Whatsminer M50", "114", 3306),
    ("MicroBT", "Whatsminer M50S", "126", 3276),
    ("MicroBT", "Whatsminer M53", "230", 6612),
    ("MicroBT", "Whatsminer M60", "172", 3422),
    ("MicroBT", "Whatsminer M60S", "186", 3441),
    ("MicroBT", "Whatsminer M66", "298", 5513),
]


def _existing_keys(bind: Connection | Session) -> set[tuple[str, str]]:
    rows = bind.execute(
        select(models.DeviceModel.brand, models.DeviceModel.model_name)
    ).all()
    return {(brand, model_name) for brand, model_name in rows}


def seed_device_models(bind: Connection | Engine | Session) -> int:
    """Insert any missing starter ASICs. Returns the number of rows inserted.

    Accepts an Engine (opens its own transaction), or a live Connection/Session
    (joins the caller's transaction — used by the Alembic migration and tests)."""
    # Normalize an Engine to a Connection-scoped transaction.
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            return seed_device_models(conn)

    existing = _existing_keys(bind)
    table = models.DeviceModel.__table__
    to_insert = [
        {
            "brand": brand,
            "model_name": model_name,
            "algorithm": "SHA-256",
            "coin_family": "BTC",
            "default_hashrate_ths": Decimal(hashrate),
            "default_power_w": power,
            "released_at": None,
            "is_active": True,
            "data_quality": "factory",
        }
        for brand, model_name, hashrate, power in STARTER_ASICS
        if (brand, model_name) not in existing
    ]
    if to_insert:
        bind.execute(table.insert(), to_insert)
        # Sessions need an explicit commit; Connections are committed by the
        # caller's transaction (Alembic migration / engine.begin()).
        if isinstance(bind, Session):
            bind.commit()
    return len(to_insert)


def main() -> None:
    from app.db.session import engine

    inserted = seed_device_models(engine)
    print(f"Seeded {inserted} ASIC model(s).")


if __name__ == "__main__":
    main()
