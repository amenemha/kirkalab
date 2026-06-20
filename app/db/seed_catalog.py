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
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger(__name__)

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "asic_catalog.json"
)

# Hard limits of the NUMERIC columns on ``device_models`` (precision, scale).
# Postgres rejects any value whose absolute integral part is >= 10**(p-s),
# while SQLite silently accepts it — which is exactly why a bad row passed CI
# but crashed the production migration. We sanitize before insert so a single
# malformed record can never abort the whole catalog import (and prod startup).
_NUMERIC_LIMITS: dict[str, tuple[int, int]] = {
    "default_hashrate_ths": (12, 2),
    "efficiency_j_per_th": (12, 4),
    "noise_db": (6, 2),
    "weight_kg": (8, 3),
}

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


def _fits_numeric(value: Decimal, precision: int, scale: int) -> bool:
    """Whether ``value`` rounds into a NUMERIC(precision, scale) column.

    Mirrors Postgres' check: the absolute value rounded to ``scale`` decimals
    must be strictly less than ``10**(precision - scale)``.
    """
    limit = Decimal(10) ** (precision - scale)
    return abs(value.quantize(Decimal(1).scaleb(-scale))) < limit


def _is_ths(unit: object) -> bool:
    return str(unit or "").strip().lower() == "th/s"


def _sanitize_numeric(
    field: str, value: Decimal | None, entry: dict
) -> Decimal | None:
    """Drop a NUMERIC value that would overflow its column, logging a warning.

    Returns ``None`` for out-of-range values so the row still imports instead
    of aborting the whole transaction (the bug that took prod down)."""
    if value is None:
        return None
    precision, scale = _NUMERIC_LIMITS[field]
    if not _fits_numeric(value, precision, scale):
        logger.warning(
            "catalog seed: dropping out-of-range %s=%s for %s / %s "
            "(exceeds NUMERIC(%d,%d))",
            field,
            value,
            entry.get("brand"),
            entry.get("model_name"),
            precision,
            scale,
        )
        return None
    return value


def _row_values(entry: dict) -> dict:
    """Map a catalog JSON object onto DeviceModel column values."""
    hashrate_unit = entry.get("hashrate_unit") or "TH/s"
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

    # efficiency_j_per_th (J per TH/s) is only meaningful for TH/s devices.
    # For any other unit the figure is garbage and frequently astronomical
    # (e.g. Innosilicon A8 CryptoMaster = 2.19e9), overflowing NUMERIC(12,4).
    eff = values.get("efficiency_j_per_th")
    if eff is not None and not _is_ths(hashrate_unit):
        logger.warning(
            "catalog seed: clearing efficiency_j_per_th=%s for non-TH/s "
            "device %s / %s (unit=%s)",
            eff,
            entry.get("brand"),
            entry.get("model_name"),
            hashrate_unit,
        )
        values["efficiency_j_per_th"] = None

    # Clamp every NUMERIC column to its column limits as a last-resort guard,
    # so a single bad record never aborts the import / prod startup.
    for field in _NUMERIC_LIMITS:
        current = values.get(field)
        if isinstance(current, Decimal):
            sanitized = _sanitize_numeric(field, current, entry)
            # default_hashrate_ths is NOT NULL — never drop it to None.
            if sanitized is None and field == "default_hashrate_ths":
                sanitized = Decimal("0")
            values[field] = sanitized
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
