"""Idempotent seed of system firmware presets (overclock/undervolt points).

Loads ``app/data/firmware_presets.json`` and upserts each preset by
``(device_model_id, firmware, preset_name)``. Presets are attached to an
existing ``device_models`` row by ``(brand, model_name, variant)``; if the
model is missing the preset block is skipped with a log (never raises), so the
seed stays safe to run before the full catalog import has landed.

Re-running never creates duplicates and refreshes preset fields in place.

Run standalone with ``python -m app.db.seed_firmware`` or let it run as part of
Alembic migration ``0006`` (which passes a live connection).
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

logger = logging.getLogger("app.seed_firmware")

PRESETS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "firmware_presets.json"
)

SEED_NOTE = "ориентировочно, требует верификации"


def load_presets() -> list[dict]:
    with PRESETS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _efficiency(hashrate: Decimal | None, power_w: Decimal | None) -> Decimal | None:
    if hashrate is None or power_w is None or hashrate <= 0:
        return None
    return (power_w / hashrate).quantize(Decimal("0.0001"))


def seed_firmware_presets(bind: Connection | Engine | Session) -> dict[str, int]:
    """Upsert firmware presets. Returns counts of inserted/updated/skipped.

    Accepts an Engine (opens its own transaction) or a live Connection/Session
    (joins the caller's transaction — used by the Alembic migration and tests).
    """
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            return seed_firmware_presets(conn)

    model_table = models.DeviceModel
    preset_table = models.FirmwarePreset.__table__

    # Resolve device models by (brand, model_name, variant).
    model_rows = bind.execute(
        select(
            model_table.id,
            model_table.brand,
            model_table.model_name,
            model_table.variant,
        )
    ).all()
    model_id_by_key: dict[tuple[str, str, str | None], int] = {
        (brand, model_name, variant): row_id
        for row_id, brand, model_name, variant in model_rows
    }

    # Snapshot existing presets keyed for idempotent upsert.
    existing = bind.execute(
        select(
            preset_table.c.id,
            preset_table.c.device_model_id,
            preset_table.c.firmware,
            preset_table.c.preset_name,
        )
    ).all()
    existing_id: dict[tuple[int, str, str], int] = {
        (dm_id, fw, name): pid for pid, dm_id, fw, name in existing
    }

    inserted = 0
    updated = 0
    skipped = 0
    for block in load_presets():
        key = (block["brand"], block["model_name"], block.get("variant"))
        device_model_id = model_id_by_key.get(key)
        if device_model_id is None:
            logger.warning("firmware seed: device model not found for %s, skipping", key)
            skipped += len(block.get("presets", []))
            continue

        for preset in block.get("presets", []):
            hashrate = _to_decimal(preset.get("hashrate"))
            power_w = _to_decimal(preset.get("power_w"))
            values = {
                "device_model_id": device_model_id,
                "firmware": preset["firmware"],
                "preset_name": preset["preset_name"],
                "mode": preset["mode"],
                "hashrate": hashrate or Decimal("0"),
                "hashrate_unit": preset.get("hashrate_unit") or "TH/s",
                "power_w": power_w or Decimal("0"),
                "efficiency_j_per_th": _efficiency(hashrate, power_w),
                "is_system": True,
                "source_url": preset.get("source_url"),
                "notes": preset.get("notes") or SEED_NOTE,
            }
            upsert_key = (device_model_id, values["firmware"], values["preset_name"])
            target_id = existing_id.get(upsert_key)
            if target_id is not None:
                bind.execute(
                    preset_table.update()
                    .where(preset_table.c.id == target_id)
                    .values(**values)
                )
                updated += 1
            else:
                result = bind.execute(preset_table.insert().values(**values))
                existing_id[upsert_key] = result.inserted_primary_key[0]
                inserted += 1

    if isinstance(bind, Session):
        bind.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def main() -> None:
    from app.db.session import engine

    counts = seed_firmware_presets(engine)
    print(
        f"Firmware preset seed: {counts['inserted']} inserted, "
        f"{counts['updated']} updated, {counts['skipped']} skipped."
    )


if __name__ == "__main__":
    main()
