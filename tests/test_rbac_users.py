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



def _make_admin(client, db, prefix="admin"):
    email, handle = _creds(prefix)
    created = _create_user(client, email, handle)
    user = db.get(models.User, created["id"])
    user.is_admin = True
    db.commit()
    token = _login(client, email)
    return token, created


def test_admin_can_get_user_by_id(client, db):
    admin_token, _ = _make_admin(client, db)
    email, handle = _creds("target")
    target = _create_user(client, email, handle)
    resp = client.get(
        f"/api/v1/users/{target['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == target["id"]


def test_get_user_not_found(client, db):
    admin_token, _ = _make_admin(client, db)
    resp = client.get(
        "/api/v1/users/99999999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404, resp.text


def test_non_admin_cannot_get_user(client):
    email, handle = _creds("plain")
    created = _create_user(client, email, handle)
    token = _login(client, email)
    resp = client.get(
        f"/api/v1/users/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


def test_admin_can_delete_user(client, db):
    admin_token, _ = _make_admin(client, db)
    email, handle = _creds("victim")
    target = _create_user(client, email, handle)
    resp = client.delete(
        f"/api/v1/users/{target['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 204, resp.text
    resp = client.get(
        f"/api/v1/users/{target['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404, resp.text


def test_non_admin_cannot_delete_user(client):
    email, handle = _creds("plain")
    created = _create_user(client, email, handle)
    token = _login(client, email)
    resp = client.delete(
        f"/api/v1/users/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text



def test_admin_can_update_user(client, db):
    admin_token, _ = _make_admin(client, db)
    email, handle = _creds("patchme")
    target = _create_user(client, email, handle)
    resp = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"handle": "updated_handle"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == target["id"]
    assert body["handle"] == "updated_handle"
    assert body["email"] == email


def test_admin_can_update_password(client, db):
    admin_token, _ = _make_admin(client, db)
    email, handle = _creds("pwd")
    target = _create_user(client, email, handle)
    new_password = "NewSecret456789"
    resp = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"password": new_password},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": new_password},
    )
    assert resp.status_code == 200, resp.text


def test_update_user_not_found(client, db):
    admin_token, _ = _make_admin(client, db)
    resp = client.patch(
        "/api/v1/users/99999999",
        json={"handle": "nobody"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404, resp.text


def test_non_admin_cannot_update_user(client):
    email, handle = _creds("plain")
    created = _create_user(client, email, handle)
    token = _login(client, email)
    resp = client.patch(
        f"/api/v1/users/{created['id']}",
        json={"handle": "hacked"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


def test_update_user_duplicate_email(client, db):
    admin_token, _ = _make_admin(client, db)
    email_a, handle_a = _creds("usera")
    _create_user(client, email_a, handle_a)
    email_b, handle_b = _creds("userb")
    target = _create_user(client, email_b, handle_b)
    resp = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"email": email_a},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400, resp.text


def test_update_user_cannot_escalate_admin(client, db):
    admin_token, _ = _make_admin(client, db)
    email, handle = _creds("escalate")
    target = _create_user(client, email, handle)
    resp = client.patch(
        f"/api/v1/users/{target['id']}",
        json={"is_admin": True, "is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    user = db.get(models.User, target["id"])
    assert user.is_admin is False
    assert user.is_active is True
