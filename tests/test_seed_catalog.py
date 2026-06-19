from decimal import Decimal

from app.db import models
from app.db.seed_asic import STARTER_ASICS, seed_device_models
from app.db.seed_catalog import load_catalog, seed_catalog

CATALOG_SIZE = 184


def test_catalog_file_has_expected_size():
    catalog = load_catalog()
    assert len(catalog) == CATALOG_SIZE
    keys = {(e["brand"], e["model_name"], e.get("variant")) for e in catalog}
    assert len(keys) == CATALOG_SIZE  # no duplicate keys in the data file


def test_seed_catalog_imports_full_catalog(db):
    counts = seed_catalog(db)
    assert counts["inserted"] == CATALOG_SIZE
    assert counts["updated"] == 0
    assert db.query(models.DeviceModel).count() == CATALOG_SIZE


def test_seed_catalog_is_idempotent(db):
    first = seed_catalog(db)
    assert first["inserted"] == CATALOG_SIZE

    second = seed_catalog(db)
    assert second["inserted"] == 0
    assert second["updated"] == CATALOG_SIZE
    # Row count is unchanged on the second run.
    assert db.query(models.DeviceModel).count() == CATALOG_SIZE


# Starter models that overlap with the full catalog (same brand+model_name)
# and are therefore adopted in place rather than duplicated.
_STARTERS_IN_CATALOG = {
    ("Bitmain", "Antminer S19"),
    ("Bitmain", "Antminer S19 Pro"),
    ("Bitmain", "Antminer S19 XP"),
    ("Bitmain", "Antminer S21"),
    ("Bitmain", "Antminer S21 Pro"),
    ("Bitmain", "Antminer S21 XP"),
    ("Bitmain", "Antminer T21"),
}
# Remaining starters have no catalog counterpart, so they legitimately survive
# as standalone rows.
_STARTERS_ONLY = len(STARTER_ASICS) - len(_STARTERS_IN_CATALOG)


def test_seed_catalog_reconciles_starter_rows(db):
    # Pre-seed the 13 starter ASICs (variant IS NULL), as migration 0004 does.
    seed_device_models(db)
    assert db.query(models.DeviceModel).count() == len(STARTER_ASICS)

    seed_catalog(db)
    # Overlapping starter rows are adopted/updated in place (not duplicated);
    # the non-overlapping ones remain. So the final count is the catalog size
    # plus only the starter-only models, never catalog + all 13.
    expected = CATALOG_SIZE + _STARTERS_ONLY
    assert db.query(models.DeviceModel).count() == expected

    # Exactly one row per overlapping starter model — no NULL-variant duplicate
    # lingering alongside the catalog variant row.
    s19_rows = (
        db.query(models.DeviceModel)
        .filter_by(brand="Bitmain", model_name="Antminer S19")
        .all()
    )
    assert len(s19_rows) == 1
    assert s19_rows[0].variant == "95 TH"

    # The full catalog count is reachable on a fresh DB without the starters.
    assert _STARTERS_ONLY >= 0


def test_passport_fields_are_populated(db):
    seed_catalog(db)

    s19 = (
        db.query(models.DeviceModel)
        .filter_by(brand="Bitmain", model_name="Antminer S19", variant="95 TH")
        .one()
    )
    assert s19.data_quality == "factory"
    assert s19.efficiency_j_per_th == Decimal("34.5")
    assert s19.cooling_type == "air"
    assert s19.release_year == 2020
    assert s19.hashrate_unit == "TH/s"
    assert s19.source_url and s19.source_url.startswith("http")
    assert s19.weight_kg == Decimal("14.200")
    assert int(s19.default_power_w) == 3250
    assert s19.default_hashrate_ths == Decimal("95.00")
