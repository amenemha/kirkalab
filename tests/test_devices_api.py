from app.db.seed_asic import STARTER_ASICS, seed_device_models


def test_list_models_empty_by_default(client):
    resp = client.get("/api/v1/devices/models")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_list_and_get_models_after_seed(client, db):
    seed_device_models(db)

    resp = client.get("/api/v1/devices/models")
    assert resp.status_code == 200, resp.text
    models = resp.json()
    assert len(models) == len(STARTER_ASICS)

    first_id = models[0]["id"]
    detail = client.get(f"/api/v1/devices/models/{first_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data_quality"] == "factory"


def test_get_missing_model_404(client):
    resp = client.get("/api/v1/devices/models/999999")
    assert resp.status_code == 404
