from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import models

BOT_SECRET = "test-bot-secret"


def _start(client):
    resp = client.post("/api/v1/auth/qr/start")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve(client, session_id, telegram_user_id=12345, secret=BOT_SECRET):
    headers = {"X-Bot-Secret": secret} if secret is not None else {}
    return client.post(
        "/api/v1/auth/qr/approve",
        json={"session_id": session_id, "telegram_user_id": telegram_user_id},
        headers=headers,
    )


def test_start_returns_session_and_deep_link(client):
    body = _start(client)
    assert body["session_id"]
    assert len(body["session_id"]) >= 32
    assert body["deep_link"] == (
        f"https://t.me/roibot_ai_bot?start=qr_{body['session_id']}"
    )
    assert body["expires_at"]


def test_status_pending_before_approval(client):
    session_id = _start(client)["session_id"]
    resp = client.get(f"/api/v1/auth/qr/status/{session_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "pending",
        "access_token": None,
        "refresh_token": None,
        "token_type": None,
    }


def test_status_unknown_session_404(client):
    resp = client.get("/api/v1/auth/qr/status/does-not-exist")
    assert resp.status_code == 404, resp.text


def test_approve_with_valid_secret_creates_user(client, db):
    session_id = _start(client)["session_id"]
    resp = _approve(client, session_id, telegram_user_id=98765)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    user = db.scalar(
        select(models.User).where(models.User.telegram_user_id == 98765)
    )
    assert user is not None
    assert user.email == "tg_98765@telegram.bot"
    assert user.is_active is True


def test_approve_rejects_wrong_secret(client):
    session_id = _start(client)["session_id"]
    resp = _approve(client, session_id, secret="wrong-secret")
    assert resp.status_code == 403, resp.text


def test_approve_rejects_missing_secret(client):
    session_id = _start(client)["session_id"]
    resp = _approve(client, session_id, secret=None)
    assert resp.status_code == 403, resp.text


def test_status_returns_tokens_once_after_approval(client):
    session_id = _start(client)["session_id"]
    assert _approve(client, session_id).status_code == 200

    first = client.get(f"/api/v1/auth/qr/status/{session_id}")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "approved"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"

    # Second poll must not hand out tokens again (one-time consumption).
    second = client.get(f"/api/v1/auth/qr/status/{session_id}")
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "consumed"
    assert second.json()["access_token"] is None


def test_issued_token_authenticates(client):
    session_id = _start(client)["session_id"]
    _approve(client, session_id, telegram_user_id=55555)
    token = client.get(f"/api/v1/auth/qr/status/{session_id}").json()["access_token"]
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "tg_55555@telegram.bot"


def test_expired_session_reports_expired_on_status(client, db):
    session_id = _start(client)["session_id"]
    session = db.scalar(
        select(models.QrLoginSession).where(
            models.QrLoginSession.session_id == session_id
        )
    )
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(session)
    db.commit()

    resp = client.get(f"/api/v1/auth/qr/status/{session_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "expired"


def test_expired_session_cannot_be_approved(client, db):
    session_id = _start(client)["session_id"]
    session = db.scalar(
        select(models.QrLoginSession).where(
            models.QrLoginSession.session_id == session_id
        )
    )
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(session)
    db.commit()

    resp = _approve(client, session_id)
    assert resp.status_code == 409, resp.text


def test_approve_rejects_non_positive_telegram_id(client):
    session_id = _start(client)["session_id"]
    resp = _approve(client, session_id, telegram_user_id=0)
    assert resp.status_code == 422, resp.text


def test_approve_rejects_negative_telegram_id(client):
    session_id = _start(client)["session_id"]
    resp = _approve(client, session_id, telegram_user_id=-5)
    assert resp.status_code == 422, resp.text


def test_approve_rejects_out_of_range_telegram_id(client):
    session_id = _start(client)["session_id"]
    resp = _approve(client, session_id, telegram_user_id=2**63)
    assert resp.status_code == 422, resp.text


def test_reused_telegram_id_maps_to_same_user(client, db):
    s1 = _start(client)["session_id"]
    _approve(client, s1, telegram_user_id=77777)
    s2 = _start(client)["session_id"]
    _approve(client, s2, telegram_user_id=77777)

    users = db.scalars(
        select(models.User).where(models.User.telegram_user_id == 77777)
    ).all()
    assert len(users) == 1
