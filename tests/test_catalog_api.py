"""API tests for the bot catalog endpoints: brands, paginated models per
brand, and the firmware-presets lookup used by the device card."""
from app.db.seed_catalog import seed_catalog
from app.db.seed_firmware import seed_firmware_presets


def test_brands_empty_by_default(client):
    resp = client.get("/api/v1/devices/brands")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_brands_after_seed(client, db):
    seed_catalog(db)

    resp = client.get("/api/v1/devices/brands")
    assert resp.status_code == 200, resp.text
    brands = resp.json()
    names = [b["brand"] for b in brands]
    # Sorted alphabetically, with a positive model count each.
    assert names == sorted(names)
    assert {"Bitmain", "MicroBT", "Innosilicon", "Canaan"} <= set(names)
    by_name = {b["brand"]: b["model_count"] for b in brands}
    assert by_name["Bitmain"] == 63
    assert all(count > 0 for count in by_name.values())


def test_models_by_brand_pagination(client, db):
    seed_catalog(db)

    first = client.get("/api/v1/devices/brands/Bitmain/models?skip=0&limit=8")
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert page1["brand"] == "Bitmain"
    assert page1["total"] == 63
    assert page1["skip"] == 0
    assert page1["limit"] == 8
    assert len(page1["items"]) == 8

    second = client.get("/api/v1/devices/brands/Bitmain/models?skip=8&limit=8")
    page2 = second.json()
    assert len(page2["items"]) == 8
    # No overlap between consecutive pages.
    ids1 = {m["id"] for m in page1["items"]}
    ids2 = {m["id"] for m in page2["items"]}
    assert ids1.isdisjoint(ids2)

    # Last page returns the remainder only.
    last_skip = (page1["total"] // 8) * 8
    last = client.get(
        f"/api/v1/devices/brands/Bitmain/models?skip={last_skip}&limit=8"
    )
    remainder = page1["total"] - last_skip
    assert len(last.json()["items"]) == remainder


def test_models_by_brand_unknown_brand(client, db):
    seed_catalog(db)
    resp = client.get("/api/v1/devices/brands/Nope/models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_model_card_has_passport_fields(client, db):
    seed_catalog(db)
    page = client.get("/api/v1/devices/brands/Bitmain/models?limit=1").json()
    model_id = page["items"][0]["id"]

    detail = client.get(f"/api/v1/devices/models/{model_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["brand"] == "Bitmain"
    # Passport fields are present in the schema (possibly null).
    for field in ("hashrate_unit", "algorithm", "efficiency_j_per_th"):
        assert field in body


def test_firmware_presets_present_for_tuned_model(client, db):
    seed_catalog(db)
    seed_firmware_presets(db)

    # Find the S19 XP (it has seeded presets).
    page = client.get(
        "/api/v1/devices/brands/Bitmain/models?limit=100"
    ).json()
    s19xp = next(
        m for m in page["items"] if m["model_name"] == "Antminer S19 XP"
    )

    resp = client.get(
        f"/api/v1/firmware/presets?device_model_id={s19xp['id']}"
    )
    assert resp.status_code == 200, resp.text
    presets = resp.json()
    assert presets
    assert all(p["device_model_id"] == s19xp["id"] for p in presets)


def test_firmware_presets_absent_returns_empty(client, db):
    seed_catalog(db)
    seed_firmware_presets(db)

    # Pick a model that has no seeded firmware presets.
    page = client.get(
        "/api/v1/devices/brands/Innosilicon/models?limit=1"
    ).json()
    model_id = page["items"][0]["id"]

    resp = client.get(
        f"/api/v1/firmware/presets?device_model_id={model_id}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
