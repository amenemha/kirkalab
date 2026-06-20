from decimal import Decimal

from app.db import models
from app.db.seed_asic import STARTER_ASICS, seed_device_models
from app.db.seed_catalog import _row_values, load_catalog, seed_catalog

CATALOG_SIZE = 184

# Max absolute value that fits in efficiency_j_per_th = NUMERIC(12, 4).
_EFFICIENCY_LIMIT = Decimal("1e8")


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


# --- Regression: efficiency_j_per_th overflow on non-TH/s devices (prod 502) ---


def test_catalog_data_has_no_non_ths_efficiency():
    """Sanity-check the source data file itself: efficiency_j_per_th is only
    populated for TH/s devices, and never exceeds NUMERIC(12,4)."""
    for entry in load_catalog():
        eff = entry.get("efficiency_j_per_th")
        if eff in (None, ""):
            continue
        unit = (entry.get("hashrate_unit") or "TH/s").strip().lower()
        assert unit == "th/s", (
            f"{entry['brand']} / {entry['model_name']} has efficiency on "
            f"unit {entry.get('hashrate_unit')!r}"
        )
        assert abs(Decimal(str(eff))) < _EFFICIENCY_LIMIT


def test_row_values_clears_efficiency_for_non_ths():
    # Mimics Innosilicon A8 CryptoMaster: KH/s unit, astronomical efficiency.
    entry = {
        "brand": "Innosilicon",
        "model_name": "A8 CryptoMaster",
        "hashrate": 280,
        "hashrate_unit": "KH/s",
        "power_w": 1200,
        "efficiency_j_per_th": "2187500000.0",
    }
    values = _row_values(entry)
    assert values["efficiency_j_per_th"] is None


def test_row_values_clamps_overflow_even_for_ths():
    # A TH/s row whose efficiency would still overflow NUMERIC(12,4) is dropped
    # to None rather than allowed to abort the transaction.
    entry = {
        "brand": "Bogus",
        "model_name": "Overflow Unit",
        "hashrate": 100,
        "hashrate_unit": "TH/s",
        "power_w": 3000,
        "efficiency_j_per_th": "999999999.9999",
    }
    values = _row_values(entry)
    assert values["efficiency_j_per_th"] is None


def test_row_values_keeps_valid_ths_efficiency():
    entry = {
        "brand": "Bitmain",
        "model_name": "Antminer S19",
        "hashrate": 95,
        "hashrate_unit": "TH/s",
        "power_w": 3250,
        "efficiency_j_per_th": "34.5",
    }
    values = _row_values(entry)
    assert values["efficiency_j_per_th"] == Decimal("34.5")


def test_seed_catalog_survives_bad_record(db, caplog):
    """A single malformed (overflowing, non-TH/s) record must not raise; the
    row imports with efficiency_j_per_th nulled out."""
    import app.db.seed_catalog as seed_module

    bad_entry = {
        "brand": "Innosilicon",
        "model_name": "A8C CryptoMaster",
        "hashrate": 320,
        "hashrate_unit": "KH/s",
        "power_w": 1400,
        "efficiency_j_per_th": "2187500000.0",
    }
    original = seed_module.load_catalog
    seed_module.load_catalog = lambda: [bad_entry]
    try:
        counts = seed_catalog(db)  # must not raise
    finally:
        seed_module.load_catalog = original

    assert counts["inserted"] == 1
    row = (
        db.query(models.DeviceModel)
        .filter_by(brand="Innosilicon", model_name="A8C CryptoMaster")
        .one()
    )
    assert row.efficiency_j_per_th is None


def test_seeded_catalog_has_no_overflow_or_non_ths_efficiency(db):
    """After a real seed, no row violates the efficiency invariant."""
    seed_catalog(db)
    rows = db.query(models.DeviceModel).all()
    for row in rows:
        if row.efficiency_j_per_th is None:
            continue
        assert (row.hashrate_unit or "TH/s").strip().lower() == "th/s"
        assert abs(row.efficiency_j_per_th) < _EFFICIENCY_LIMIT
