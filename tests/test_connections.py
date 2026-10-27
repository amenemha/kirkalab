import httpx

from app.core.config import get_settings
from app.services import connections as cn
from app.services import support as sup
from tests.test_rbac_users import _create_user, _creds, _login, _make_admin
from app.db import models

SENT: list = []
DEVS = [
    {"uniqId": "d1", "status": "working", "hashRateMHS": 200e6, "platform": {"uniqId": "p1", "name": "Сибирь"}, "container": {"uniqId": "c1", "name": "1а"}},
    {"uniqId": "d2", "status": "offline", "platform": {"uniqId": "p1", "name": "Сибирь"}, "container": {"uniqId": "c2", "name": "2б"}},
    {"uniqId": "d3", "status": "working", "hashRateMHS": 100e6, "platform": {"uniqId": "p2", "name": "Урал"}, "container": {"uniqId": "c3", "name": "У1"}},
]


class FakeCroft:
    def __init__(self, login, pw):
        self.ok = pw == "good"

    def view_groups(self):
        if not self.ok:
            req = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
        return [{"customer": {"uniqId": "cust1"}}]

    def devices(self, customer):
        assert customer == "cust1"
        return DEVS


class FakeVia:
    def info(self, key):
        if key == "bad":
            raise RuntimeError("no")
        return {"account": "acc1"}

    def home(self, key, coin, uid):
        return {"hashrate_1hour": "120000000000000", "total_active": 3, "total_unactive": 1}

    def hourly(self, key, coin, uid):
        return {}

    def workers(self, *a, **k):
        return []


def _setup(client, db, monkeypatch):
    SENT.clear()
    monkeypatch.setattr(sup, "tg_send", lambda chat, t, reply_button=True: SENT.append((chat, t)) or 1)
    monkeypatch.setattr(cn, "_croft_client", FakeCroft)
    s = get_settings()
    monkeypatch.setattr(s, "bot_internal_secret", "sek")
    tok, created = _make_admin(client, db)
    db.get(models.User, created["id"]).telegram_user_id = 555
    email, handle = _creds("farmer")
    c = _create_user(client, email, handle)
    db.get(models.User, c["id"]).telegram_user_id = 777
    db.commit()
    return {"Authorization": f"Bearer {tok}"}, {"Authorization": f"Bearer {_login(client, email)}"}


def test_croft_connect_flow(client, db, monkeypatch):
    ha, hu = _setup(client, db, monkeypatch)
    r = client.post("/api/v1/me/connections/croft", json={"login": "me", "password": "bad"}, headers=hu)
    assert r.status_code == 400 and "логин или пароль" in r.json()["detail"]
    r = client.post("/api/v1/me/connections/croft", json={"login": "me", "password": "good"}, headers=hu)
    assert r.status_code == 200, r.text
    st = r.json()["stats"]
    assert st["devices"] == 3 and st["working"] == 2 and st["offline"] == 1 and st["hashrate_ths"] == 300 and "300,0 TH/s" in cn.croft_text(st)
    assert st["sites"][0] == {"name": "Сибирь", "containers": 2, "devices": 2, "working": 1, "offline": 1}
    assert "password" not in r.text and "good" not in r.text
    assert SENT and SENT[-1][0] == 555 and "подключил CROFT" in SENT[-1][1] and "3 асиков" in SENT[-1][1]
    # повторное подключение того же логина не плодит записи
    client.post("/api/v1/me/connections/croft", json={"login": "me", "password": "good"}, headers=hu)
    j = client.get("/api/v1/me/connections", headers=hu).json()
    assert len(j["croft"]) == 1 and "server" not in j
    acc = j["croft"][0]
    assert client.post(f"/api/v1/me/connections/croft/{acc['id']}/check", headers=hu).json()["status"] == "ok"
    # админ видит в списке людей
    rows = client.get("/api/v1/admin/users", headers=ha).json()["users"]
    me = next(x for x in rows if x["tg_id"] == 777)
    assert me["conn"]["croft"][0]["devices"] == 3
    # чужое подключение не удалить
    assert client.delete(f"/api/v1/me/connections/croft/{acc['id']}", headers=ha).status_code == 404
    assert client.delete(f"/api/v1/me/connections/croft/{acc['id']}", headers=hu).status_code == 200
    assert client.get("/api/v1/me/connections", headers=hu).json()["croft"] == []


def test_secret_encrypted(client, db, monkeypatch):
    _setup(client, db, monkeypatch)
    from app.db.activity_models import CroftAccount
    u = db.query(models.User).filter_by(telegram_user_id=777).one()
    a = cn.croft_connect(db, u, "x", "good")
    assert a.secret != "good" and cn.dec(db.get(CroftAccount, a.id).secret) == "good"


def test_recognize_links():
    r = cn.recognize("вот https://www.viabtc.com/observer/dashboard?access_key=abc123&coin=LTC спасибо")
    assert r["provider"] == "viabtc" and r["key"] == "abc123" and r["coin"] == "LTC" and r["live"]
    r = cn.recognize("https://www.f2pool.com/btc/someuser")
    assert r["provider"] == "f2pool" and r["key"] == "someuser" and r["coin"] == "BTC"
    r = cn.recognize("https://www.antpool.com/observer?accessKey=K1&coinType=BTC&observerUserId=x")
    assert r["provider"] == "antpool" and r["key"] == "K1"
    r = cn.recognize("https://www.viabtc.com/user/dashboard")
    assert r["private"]
    assert cn.recognize("https://google.com/?access_key=1") is None
    assert cn.recognize("просто текст") is None


def test_pool_connect(client, db, monkeypatch):
    _, hu = _setup(client, db, monkeypatch)
    from app.services.pools import viabtc
    monkeypatch.setattr(viabtc, "ViaBTC", FakeVia)
    r = client.post("/api/v1/me/connections/pool", json={"url": "https://www.viabtc.com/observer/dashboard?access_key=k9&coin=BTC"}, headers=hu)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["found"] and j["account"]["workers_active"] == 3 and j["account"]["hs_1h"] == 1.2e14
    assert "ссылку наблюдателя ViaBTC" in SENT[-1][1]
    again = client.post("/api/v1/me/connections/pool", json={"url": "https://www.viabtc.com/observer/dashboard?access_key=k9&coin=BTC"}, headers=hu).json()
    assert again["already"]
    r = client.post("/api/v1/me/connections/pool", json={"url": "https://www.viabtc.com/user/dashboard"}, headers=hu)
    assert r.status_code == 400 and "наблюдателя" in r.json()["detail"]
    r = client.post("/api/v1/me/connections/pool", json={"url": "https://www.f2pool.com/ltc/bob"}, headers=hu).json()
    assert not r["found"] and not r["live"] and r["account"]["name"] == "F2Pool"
    pools = client.get("/api/v1/me/connections", headers=hu).json()["pools"]
    assert len(pools) == 2
    for p in pools:
        assert client.delete(f"/api/v1/me/connections/pool/{p['id']}", headers=hu).status_code == 200


def test_internal_bot(client, db, monkeypatch):
    _setup(client, db, monkeypatch)
    H = {"X-Bot-Secret": "sek"}
    r = client.post("/api/v1/internal/connections", json={"action": "croft", "tg_id": 777, "login": "a", "password": "bad"}, headers=H).json()
    assert not r["ok"] and "пароль" in r["error"]
    r = client.post("/api/v1/internal/connections", json={"action": "croft", "tg_id": 777, "login": "a", "password": "good"}, headers=H).json()
    assert r["ok"] and "3 асиков" in r["text"] and "Сибирь (2)" in r["text"]
    r = client.post("/api/v1/internal/connections", json={"action": "list", "tg_id": 555}, headers=H).json()
    assert r["is_admin"] and "server" in r
    assert client.post("/api/v1/internal/connections", json={"action": "recognize", "text": "https://www.f2pool.com/btc/x"}, headers=H).json()["pool"]["provider"] == "f2pool"
    assert client.post("/api/v1/internal/connections", json={"action": "list", "tg_id": 777}, headers={"X-Bot-Secret": "no"}).status_code in (401, 403)
