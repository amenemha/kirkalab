from uuid import uuid4

PASSWORD = "123456Test789"


def _creds(prefix: str = "sec"):
    suffix = uuid4().hex[:8]
    return f"{prefix}_{suffix}@example.com", f"{prefix}_{suffix}"


def _create_user(client, email, handle):
    resp = client.post(
        "/api/v1/users/",
        json={"email": email, "handle": handle, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(client, email, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )


def test_login_returns_access_and_refresh_tokens(client):
    email, handle = _creds()
    _create_user(client, email, handle)
    resp = _login(client, email)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_refresh_returns_new_tokens(client):
    email, handle = _creds()
    _create_user(client, email, handle)
    refresh_token = _login(client, email).json()["refresh_token"]
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_refresh_rejects_access_token(client):
    email, handle = _creds()
    _create_user(client, email, handle)
    access_token = _login(client, email).json()["access_token"]
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},
    )
    assert resp.status_code == 401, resp.text


def test_refresh_rejects_garbage(client):
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not.a.token"},
    )
    assert resp.status_code == 401, resp.text


def _auth_header(client, email):
    token = _login(client, email).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_email_verification_flow(client):
    email, handle = _creds()
    _create_user(client, email, handle)
    headers = _auth_header(client, email)
    req = client.post("/api/v1/auth/verify-email/request", headers=headers)
    assert req.status_code == 200, req.text
    token = req.json()["email_verify_token"]
    resp = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == email


def test_verify_email_rejects_invalid_token(client):
    resp = client.post("/api/v1/auth/verify-email", json={"token": "bad"})
    assert resp.status_code == 400, resp.text


def test_password_reset_flow(client):
    email, handle = _creds()
    _create_user(client, email, handle)
    req = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": email},
    )
    assert req.status_code == 200, req.text
    token = req.json()["reset_token"]
    new_password = "BrandNew99999"
    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": new_password},
    )
    assert resp.status_code == 200, resp.text
    # Old password no longer works, new one does.
    assert _login(client, email, PASSWORD).status_code == 401
    assert _login(client, email, new_password).status_code == 200


def test_password_reset_request_unknown_email(client):
    resp = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "nobody@example.com"},
    )
    # Same generic response, no enumeration, and no token leaked.
    assert resp.status_code == 200, resp.text
    assert "reset_token" not in resp.json()


def test_password_reset_confirm_rejects_invalid_token(client):
    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "bad", "new_password": "BrandNew99999"},
    )
    assert resp.status_code == 400, resp.text
