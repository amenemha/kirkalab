from app.core.config import get_settings
from app.services import activity as act


def _admin(client, db):
    from app.db import models
    from tests.test_rbac_users import _make_admin
    tok, created = _make_admin(client, db)
    u = db.get(models.User, created["id"])
    u.telegram_user_id = 555
    db.commit()
    return {"Authorization": f"Bearer {tok}"}


def test_labels():
    assert act.bot_label("/start qr_abc") == "Запустил бота (/start)"
    assert act.bot_label("hub:farm:r") == "Ферма"
    assert act.bot_label("calc:m:12") == "Калькулятор · выбрал модель"
    assert act.bot_label("notif:test") == "Уведомления · проверка"
    assert act.site_label("cont", "11к") == "Карта контейнера · 11к"


def test_bot_activity_and_admin(client, db, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "bot_internal_secret", "sek")
    monkeypatch.setattr(s, "bot_token", "")
    h = _admin(client, db)
    r = client.post("/api/v1/internal/activity", json={"telegram_user_id": 777, "action": "/start"}, headers={"X-Bot-Secret": "sek"})
    assert r.status_code == 200
    client.post("/api/v1/internal/activity", json={"telegram_user_id": 777, "action": "hub:farm"}, headers={"X-Bot-Secret": "sek"})
    assert client.post("/api/v1/internal/activity", json={"telegram_user_id": 777, "action": "x"}, headers={"X-Bot-Secret": "bad"}).status_code == 403
    assert client.post("/api/v1/activity", json={"page": "energy"}, headers=h).status_code == 200
    j = client.get("/api/v1/admin/users", headers=h).json()
    by = {u["tg_id"]: u for u in j["users"]}
    assert by[777]["started"] and by[777]["saw_farm"] and by[777]["bot_actions"] == 2
    assert by[555]["site_views"] == 1
    feed = client.get("/api/v1/admin/activity?tg=777", headers=h).json()["items"]
    assert [x["label"] for x in feed] == ["Ферма", "Запустил бота (/start)"]


def test_admin_forbidden_for_user(client):
    from tests.test_rbac_users import _creds, _create_user, _login
    email, handle = _creds("plain")
    _create_user(client, email, handle)
    tok = _login(client, email)
    assert client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_notify_sent_is_logged(client, db, monkeypatch):
    from app.services.notify import service as ns
    monkeypatch.setattr(ns, "send_telegram", lambda *a, **k: True)
    h = _admin(client, db)
    assert ns.send_test(db, 555)
    items = client.get("/api/v1/farm/notify/log", headers=h).json()["items"]
    assert items and items[0]["label"] == "Проверка уведомлений"
