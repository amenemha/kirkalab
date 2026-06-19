from uuid import uuid4

from app.db import models
from app.db.seed_catalog import seed_catalog

PASSWORD = "123456Test789"


def _register(client, db, *, is_pro: bool):
    email = f"u_{uuid4().hex[:8]}@example.com"
    handle = f"u_{uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/users/",
        json={"email": email, "handle": handle, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    if is_pro:
        user = db.query(models.User).filter_by(email=email).one()
        user.is_pro = True
        db.commit()
    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["access_token"]


def _a_model_id(db):
    seed_catalog(db)
    return db.query(models.DeviceModel).first().id


def _build_payload(model_id, **overrides):
    data = {
        "device_model_id": model_id,
        "build_name": "Vnish разгон",
        "firmware": "vnish",
        "mode": "overclock",
        "hashrate": "158",
        "power_w": "3620",
        "notes": "моя сборка",
    }
    data.update(overrides)
    return data


def test_pro_can_create_and_read_build(client, db):
    model_id = _a_model_id(db)
    token = _register(client, db, is_pro=True)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/api/v1/firmware/builds", json=_build_payload(model_id), headers=headers
    )
    assert resp.status_code == 201, resp.text
    build = resp.json()
    assert build["build_name"] == "Vnish разгон"
    assert build["device_model_id"] == model_id

    listed = client.get("/api/v1/firmware/builds", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    got = client.get(f"/api/v1/firmware/builds/{build['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == build["id"]


def test_non_pro_cannot_create_build(client, db):
    model_id = _a_model_id(db)
    token = _register(client, db, is_pro=False)
    resp = client.post(
        "/api/v1/firmware/builds",
        json=_build_payload(model_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert "PRO" in resp.json()["detail"]


def test_build_name_is_required(client, db):
    model_id = _a_model_id(db)
    token = _register(client, db, is_pro=True)
    resp = client.post(
        "/api/v1/firmware/builds",
        json=_build_payload(model_id, build_name=""),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


def test_build_unknown_model_404(client, db):
    _a_model_id(db)
    token = _register(client, db, is_pro=True)
    resp = client.post(
        "/api/v1/firmware/builds",
        json=_build_payload(999999),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


def test_builds_are_per_user(client, db):
    model_id = _a_model_id(db)
    owner_token = _register(client, db, is_pro=True)
    other_token = _register(client, db, is_pro=True)

    created = client.post(
        "/api/v1/firmware/builds",
        json=_build_payload(model_id),
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()

    # Another user cannot see or fetch it.
    other_list = client.get(
        "/api/v1/firmware/builds",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other_list.json() == []
    other_get = client.get(
        f"/api/v1/firmware/builds/{created['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other_get.status_code == 404


def test_delete_build(client, db):
    model_id = _a_model_id(db)
    token = _register(client, db, is_pro=True)
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/firmware/builds", json=_build_payload(model_id), headers=headers
    ).json()

    resp = client.delete(
        f"/api/v1/firmware/builds/{created['id']}", headers=headers
    )
    assert resp.status_code == 204
    assert client.get("/api/v1/firmware/builds", headers=headers).json() == []


def test_anonymous_cannot_create_build(client, db):
    model_id = _a_model_id(db)
    resp = client.post("/api/v1/firmware/builds", json=_build_payload(model_id))
    assert resp.status_code in (401, 403)
