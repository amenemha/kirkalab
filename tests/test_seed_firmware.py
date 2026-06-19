from decimal import Decimal

from app.db import models
from app.db.seed_catalog import seed_catalog
from app.db.seed_firmware import load_presets, seed_firmware_presets

# 8 popular models x 3 presets each.
EXPECTED_PRESETS = 24


def test_presets_file_keys_unique():
    blocks = load_presets()
    keys = {(b["brand"], b["model_name"], b.get("variant")) for b in blocks}
    assert len(keys) == len(blocks)


def test_seed_attaches_presets_to_existing_models(db):
    seed_catalog(db)
    counts = seed_firmware_presets(db)
    assert counts["inserted"] == EXPECTED_PRESETS
    assert counts["updated"] == 0
    assert counts["skipped"] == 0
    assert db.query(models.FirmwarePreset).count() == EXPECTED_PRESETS

    # Every preset points at a real device model.
    presets = db.query(models.FirmwarePreset).all()
    model_ids = {m.id for m in db.query(models.DeviceModel).all()}
    assert all(p.device_model_id in model_ids for p in presets)
    # All seeded rows are flagged for verification.
    assert all("верификации" in (p.notes or "") for p in presets)
    assert all(p.is_system for p in presets)


def test_seed_is_idempotent(db):
    seed_catalog(db)
    first = seed_firmware_presets(db)
    assert first["inserted"] == EXPECTED_PRESETS

    second = seed_firmware_presets(db)
    assert second["inserted"] == 0
    assert second["updated"] == EXPECTED_PRESETS
    assert db.query(models.FirmwarePreset).count() == EXPECTED_PRESETS


def test_seed_skips_when_model_missing(db):
    # No catalog seeded -> every block's model is absent -> all skipped, no crash.
    counts = seed_firmware_presets(db)
    assert counts["inserted"] == 0
    assert counts["skipped"] == EXPECTED_PRESETS
    assert db.query(models.FirmwarePreset).count() == 0


def test_efficiency_is_computed(db):
    seed_catalog(db)
    seed_firmware_presets(db)
    preset = (
        db.query(models.FirmwarePreset)
        .filter_by(firmware="vnish", preset_name="Turbo")
        .first()
    )
    assert preset is not None
    expected = (preset.power_w / preset.hashrate).quantize(Decimal("0.0001"))
    assert preset.efficiency_j_per_th == expected
