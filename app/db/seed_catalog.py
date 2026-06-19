"""Idempotent seed of the full ASIC catalog (passport cards).

Loads ``app/data/asic_catalog.json`` (184 factory spec sheets) and upserts each
row by ``(brand, model_name, variant)``. Re-running never creates duplicates and
refreshes the passport fields in place.

Reconciliation with the starter catalog (``seed_asic.py``): the 13 starter rows
were seeded with ``variant = NULL``. The full catalog carries the same models
*with* a variant (e.g. "95 TH"). To avoid two rows for the same physical model,
a legacy starter row (matching ``brand`` + ``model_name`` with ``variant IS
NULL``) is adopted and updated in place rather than left as a duplicate.

Run standalone with ``python -m app.db.seed_catalog`` or let it run as part of
Alembic migration ``0005`` (which passes a live connection).
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import models

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "asic_catalog.json"
)

# JSON keys copied verbatim into same-named columns.
_PASSPORT_STRING_FIELDS = (
    "series",
    "variant",
    "hashrate_unit",
    "cooling_type",
    "voltage_input",
    "operating_temp",
    "dimensions_mm",
    "chip",
    "network",
    "max_hashrate_note",
    "source_url",
    "notes",
)
_PASSPORT_NUMERIC_FIELDS = ("efficiency_j_per_th", "noise_db", "weight_kg")


def load_catalog() -> list[dict]:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_values(entry: dict) -> dict:
    """Map a catalog JSON object onto DeviceModel column values."""
    values: dict[str, object] = {
        "brand": entry["brand"],
        "model_name": entry["model_name"],
        "algorithm": entry.get("algorithm") or "SHA-256",
        "coin_family": entry.get("coin_family") or "BTC",
        "default_hashrate_ths": _to_decimal(entry.get("hashrate")) or Decimal("0"),
        "default_power_w": _to_int(entry.get("power_w")) or 0,
        "release_year": _to_int(entry.get("release_year")),
        "data_quality": entry.get("data_quality") or "factory",
        "is_active": True,
    }
    for key in _PASSPORT_STRING_FIELDS:
        values[key] = entry.get(key)
    for key in _PASSPORT_NUMERIC_FIELDS:
        values[key] = _to_decimal(entry.get(key))
    if not values.get("hashrate_unit"):
        values["hashrate_unit"] = "TH/s"
    return values


def seed_catalog(bind: Connection | Engine | Session) -> dict[str, int]:
    """Upsert the full catalog. Returns counts of inserted/updated rows.

    Accepts an Engine (opens its own transaction), or a live
    Connection/Session (joins the caller's transaction — used by the Alembic
    migration and tests)."""
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            return seed_catalog(conn)

    table = models.DeviceModel.__table__

    # Snapshot existing rows keyed for both exact-key and legacy lookups.
    existing_rows = bind.execute(
        select(
            models.DeviceModel.id,
            models.DeviceModel.brand,
            models.DeviceModel.model_name,
            models.DeviceModel.variant,
        )
    ).all()
    by_full_key: dict[tuple[str, str, str], int] = {}
    legacy_null_variant: dict[tuple[str, str], int] = {}
    for row_id, brand, model_name, variant in existing_rows:
        by_full_key[(brand, model_name, variant or "")] = row_id
        if variant is None:
            legacy_null_variant[(brand, model_name)] = row_id

    inserted = 0
    updated = 0
    for entry in load_catalog():
        values = _row_values(entry)
        brand = values["brand"]
        model_name = values["model_name"]
        variant = values.get("variant")

        full_key = (brand, model_name, variant or "")
        target_id = by_full_key.get(full_key)
        if target_id is None:
            # Adopt a legacy starter row (same model, variant IS NULL) so we
            # update it in place instead of creating a duplicate.
            target_id = legacy_null_variant.pop((brand, model_name), None)

        if target_id is not None:
            bind.execute(
                table.update().where(table.c.id == target_id).values(**values)
            )
            by_full_key[full_key] = target_id
            updated += 1
        else:
            result = bind.execute(table.insert().values(**values))
            new_id = result.inserted_primary_key[0]
            by_full_key[full_key] = new_id
            inserted += 1

    if isinstance(bind, Session):
        bind.commit()
    return {"inserted": inserted, "updated": updated}


def main() -> None:
    from app.db.session import engine

    counts = seed_catalog(engine)
    print(
        f"Catalog seed: {counts['inserted']} inserted, "
        f"{counts['updated']} updated."
    )


if __name__ == "__main__":
    main()
