from uuid import uuid4

from app.db import models

PASSWORD = "123456Test789"


def _creds(prefix: str = "user"):
    suffix = uuid4().hex[:8]
    return f"{prefix}_{suffix}@example.com", f"{prefix}_{suffix}"


def _create_user(client, email, handle):
    resp = client.post(
        "/api/v1/users/",
        json={"email": email, "handle": handle, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(client, email):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_anonymous_cannot_list_users(client):
    resp = client.get("/api/v1/users/?skip=0&limit=10")
    assert resp.status_code in (401, 403), resp.text


def test_invalid_token_rejected(client):
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not.a.valid.token"},
    )
    assert resp.status_code == 401, resp.text


def test_non_admin_cannot_list_users(client):
    email, handle = _creds("plain")
    _create_user(client, email, handle)
    token = _login(client, email)

    resp = client.get(
        "/api/v1/users/?skip=0&limit=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Admin privileges required"


def test_admin_can_list_users(client, db):
    email, handle = _creds("admin")
    created = _create_user(client, email, handle)

    user = db.get(models.User, created["id"])
    user.is_admin = True
    db.commit()

    token = _login(client, email)
    resp = client.get(
        "/api/v1/users/?skip=0&limit=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_inactive_user_cannot_access_me(client, db):
    email, handle = _creds("inactive")
    created = _create_user(client, email, handle)
    token = _login(client, email)

    user = db.get(models.User, created["id"])
    user.is_active = False
    db.commit()

    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Inactive user"
