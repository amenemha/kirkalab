from app.db import models
from app.db.seed_catalog import seed_catalog
from app.db.seed_firmware import seed_firmware_presets


def test_list_presets_public(client, db):
    seed_catalog(db)
    seed_firmware_presets(db)

    resp = client.get("/api/v1/firmware/presets?limit=100")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 24


def test_list_presets_filtered_by_model(client, db):
    seed_catalog(db)
    seed_firmware_presets(db)
    model_id = (
        db.query(models.FirmwarePreset).first().device_model_id
    )

    resp = client.get(f"/api/v1/firmware/presets?device_model_id={model_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body
    assert all(p["device_model_id"] == model_id for p in body)
