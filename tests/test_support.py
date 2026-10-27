from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db import models
from app.services import support as sup
from tests.test_rbac_users import _create_user, _creds, _make_admin

SENT: list = []


def _fake(chat_id, text, reply_button=True):
    SENT.append((chat_id, text))
    return 1000 + len(SENT)


def _setup(client, db, monkeypatch):
    SENT.clear()
    monkeypatch.setattr(sup, "tg_send", _fake)
    s = get_settings()
    monkeypatch.setattr(s, "bot_internal_secret", "sek")
    monkeypatch.setattr(s, "bot_token", "")
    tok, created = _make_admin(client, db)
    a = db.get(models.User, created["id"])
    a.telegram_user_id = 555
    email, handle = _creds("cust")
    c = _create_user(client, email, handle)
    u = db.get(models.User, c["id"])
    u.telegram_user_id = 777
    db.commit()
    return {"Authorization": f"Bearer {tok}"}, u


def test_incoming_forward_and_reply(client, db, monkeypatch):
    h, _ = _setup(client, db, monkeypatch)
    r = client.post("/api/v1/internal/support", json={"telegram_user_id": 777, "text": "Привет, вопрос", "name": "Ваня"}, headers={"X-Bot-Secret": "sek"})
    assert r.status_code == 200 and r.json()["managers"] == 1
    assert SENT[-1][0] == 555 and "Ваня" in SENT[-1][1] and "Привет, вопрос" in SENT[-1][1]
    j = client.get("/api/v1/admin/users", headers=h).json()
    assert j["unread"] == 1 and j["users"][0]["tg_id"] == 777
    # менеджер ответил в Telegram на пересланное (message_id 1001)
    r = client.post("/api/v1/internal/support/reply", json={"manager_tg": 555, "reply_to_message_id": 1001, "text": "Ответ"}, headers={"X-Bot-Secret": "sek"})
    assert r.status_code == 200 and r.json()["to"] == 777 and SENT[-1] == (777, "Ответ")
    th = client.get("/api/v1/admin/messages?tg=777", headers=h).json()["items"]
    assert [m["dir"] for m in th] == ["in", "out"] and th[0]["status"] == "read"
    # не менеджер не может «ответить»
    assert client.post("/api/v1/internal/support/reply", json={"manager_tg": 777, "reply_to_message_id": 1001, "text": "x"}, headers={"X-Bot-Secret": "sek"}).status_code == 404


def test_broadcast(client, db, monkeypatch):
    h, _ = _setup(client, db, monkeypatch)
    r = client.post("/api/v1/admin/messages", json={"tg_ids": [777, 555, 777], "text": "Всем привет"}, headers=h).json()
    assert r["sent"] == 2 and r["broadcast"]
    assert client.post("/api/v1/admin/messages", json={"tg_ids": [777], "text": ""}, headers=h).status_code == 422


def test_access_gift_trial_extend_revoke(client, db, monkeypatch):
    h, u = _setup(client, db, monkeypatch)
    r = client.post("/api/v1/admin/access", json={"tg": 777, "action": "trial", "days": 14}, headers=h).json()
    assert r["access"]["state"] == "pro" and r["access"]["kind"] == "trial" and r["access"]["days_left"] == 14 and r["notified"]
    assert "пробный период" in SENT[-1][1]
    for w in ("купить", "оплат", "тариф", "цена", "скидк"):
        assert w not in SENT[-1][1].lower()
    r = client.post("/api/v1/admin/access", json={"tg": 777, "action": "extend", "days": 7, "notify": False}, headers=h).json()
    assert r["access"]["days_left"] == 21 and not r["notified"] and r["access"]["trial_used"]
    r = client.post("/api/v1/admin/access", json={"tg": 777, "action": "revoke"}, headers=h).json()
    assert r["access"]["state"] == "free"
    db.refresh(u)
    assert not u.is_pro
    r = client.post("/api/v1/admin/access", json={"tg": 777, "action": "gift", "days": 30}, headers=h).json()
    assert r["access"]["kind"] == "gift" and r["access"]["days_left"] == 30
    assert client.post("/api/v1/admin/access", json={"tg": 999, "action": "gift"}, headers=h).status_code == 404


def test_grant_stacks_on_remaining(db):
    u = models.User(email="s@x.ru", handle="s", hashed_password="x", telegram_user_id=1)
    db.add(u)
    db.commit()
    now = datetime(2026, 10, 27, tzinfo=UTC)
    sup.grant(db, u, 7, "trial", now=now)
    sup.grant(db, u, 7, "gift", now=now)
    assert sup._aware(u.premium_until) == now + timedelta(days=14)


def test_managers_setting(client, db, monkeypatch):
    h, _ = _setup(client, db, monkeypatch)
    assert client.get("/api/v1/admin/managers", headers=h).json()["tg_ids"] == [555]
    assert client.put("/api/v1/admin/managers", json={"tg_ids": [777]}, headers=h).json()["tg_ids"] == [777]
