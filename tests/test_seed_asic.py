from app.db import models
from app.db.seed_asic import STARTER_ASICS, seed_device_models


def test_seed_inserts_starter_catalog(db):
    inserted = seed_device_models(db)
    assert inserted == len(STARTER_ASICS)

    count = db.query(models.DeviceModel).count()
    assert count == len(STARTER_ASICS)

    # Spot-check a known model and its factory data quality.
    s19xp = (
        db.query(models.DeviceModel)
        .filter_by(brand="Bitmain", model_name="Antminer S19 XP")
        .one()
    )
    assert s19xp.data_quality == "factory"
    assert s19xp.algorithm == "SHA-256"
    assert s19xp.coin_family == "BTC"
    assert int(s19xp.default_power_w) == 3010


def test_seed_is_idempotent(db):
    first = seed_device_models(db)
    second = seed_device_models(db)
    assert first == len(STARTER_ASICS)
    assert second == 0
    assert db.query(models.DeviceModel).count() == len(STARTER_ASICS)
