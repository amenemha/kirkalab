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


def test_email_verification_request_does_not_leak_token(client, caplog):
    import logging

    email, handle = _creds()
    _create_user(client, email, handle)
    headers = _auth_header(client, email)
    with caplog.at_level(logging.INFO, logger="app.auth"):
        req = client.post("/api/v1/auth/verify-email/request", headers=headers)
    assert req.status_code == 200, req.text
    # Token must NOT be returned in the response body.
    assert "email_verify_token" not in req.json()
    assert req.json()["detail"]
    # The token is logged server-side so it can still be exercised in tests.
    token = caplog.records[-1].args[-1]
    resp = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == email


def test_verify_email_rejects_invalid_token(client):
    resp = client.post("/api/v1/auth/verify-email", json={"token": "bad"})
    assert resp.status_code == 400, resp.text


def _reset_token_from_logs(caplog):
    return caplog.records[-1].args[-1]


def test_password_reset_flow(client, caplog):
    import logging

    email, handle = _creds()
    _create_user(client, email, handle)
    with caplog.at_level(logging.INFO, logger="app.auth"):
        req = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": email},
        )
    assert req.status_code == 200, req.text
    # Token must NOT appear in the response — only in the server log.
    assert "reset_token" not in req.json()
    token = _reset_token_from_logs(caplog)
    new_password = "BrandNew99999"
    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": new_password},
    )
    assert resp.status_code == 200, resp.text
    # Old password no longer works, new one does.
    assert _login(client, email, PASSWORD).status_code == 401
    assert _login(client, email, new_password).status_code == 200


def test_password_reset_request_unknown_email_is_indistinguishable(client, caplog):
    import logging

    known_email, handle = _creds()
    _create_user(client, known_email, handle)

    with caplog.at_level(logging.INFO, logger="app.auth"):
        known = client.post(
            "/api/v1/auth/password-reset/request", json={"email": known_email}
        )
        unknown = client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "nobody@example.com"},
        )

    # Identical status + body regardless of whether the account exists.
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert "reset_token" not in unknown.json()


def test_password_change_revokes_existing_refresh_tokens(client, caplog):
    import logging

    email, handle = _creds()
    _create_user(client, email, handle)
    old_refresh = _login(client, email).json()["refresh_token"]

    # Reset the password (which bumps token_version).
    with caplog.at_level(logging.INFO, logger="app.auth"):
        client.post("/api/v1/auth/password-reset/request", json={"email": email})
    token = _reset_token_from_logs(caplog)
    client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "BrandNew99999"},
    )

    # The refresh token minted before the password change is now invalid.
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert resp.status_code == 401, resp.text


def test_refresh_rotation_invalidates_old_token(client):
    email, handle = _creds()
    _create_user(client, email, handle)
    first_refresh = _login(client, email).json()["refresh_token"]

    rotated = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh and new_refresh != first_refresh

    # Replaying the now-rotated token must fail.
    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert replay.status_code == 401, replay.text

    # The freshly issued token still works.
    again = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_refresh}
    )
    assert again.status_code == 200, again.text


def test_refresh_rejects_token_without_jti(client):
    # A refresh token forged without a jti claim must be rejected.
    from app.core.security import REFRESH, _create_token

    forged = _create_token(
        {"user_id": 1, "email": "x@example.com", "is_admin": False, "token_version": 0},
        REFRESH,
        60,
    )
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": forged})
    assert resp.status_code == 401, resp.text


def test_password_reset_confirm_rejects_invalid_token(client):
    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "bad", "new_password": "BrandNew99999"},
    )
    assert resp.status_code == 400, resp.text
