"""Уведомления с деталями: кто вернулся, воркер «было → стало», ссылка ровно на эти асики; цитата в переписке."""
from app.db import farm_models as fm
from app.services import support as sup
from app.services.notify import service as ns
from tests.test_notify import NOW, TG, _devs, _owner  # noqa: F401
from tests.test_rbac_users import _make_admin


def test_digest_lists_back_and_worker_change_and_link(db, client):
    devs = _devs(db)
    ns.set_prefs(db, TG, {"types": {"daily": False, "pool": True}})
    calls = []

    def sender(tg, text, link=None):
        calls.append((text, link))
        return True

    assert ns.tick(db, NOW, sender) == 0
    back = db.query(fm.FarmDevice).filter_by(ext_id="o0").one()
    back.status = "working"
    w = devs[3]
    db.add(fm.FarmDeviceEvent(id=1001, device_id=w.id, kind="worker", old="acc.10k-r33", new="acc.10k-r35"))
    db.add(fm.FarmDeviceEvent(id=1002, device_id=w.id, kind="pool", old="stratum+tcp://btc.viabtc.io:3333", new="stratum+tcp://btc.f2pool.com:1314"))
    db.commit()
    assert ns.tick(db, NOW + 16 * 60, sender) == 1
    text, link = calls[-1]
    assert "Вернулись в работу: 1" in text and "w.o0" in text
    assert "Сменили пул или воркер: 1" in text
    assert "пул btc.viabtc.io:3333 → btc.f2pool.com:1314 · переименован воркер — <b>1</b>" in text
    assert "<code>acc.10k-r33</code> → <code>acc.10k-r35</code>" in text and "<blockquote expandable>" in text
    assert link and link.startswith("https://kirkalab.ru/#ev/")
    tok = link.split("#ev/")[1]
    b = ns.get_batch(db, tok)
    kinds = {i["what"] for i in b["items"]}
    assert {"online", "pool"} <= kinds
    h, _ = _make_admin(client, db)
    r = client.get(f"/api/v1/farm/notify/batch/{tok}", headers={"Authorization": f"Bearer {h}"})
    assert r.status_code == 200, r.text
    rows = r.json()["items"]
    pw = next(x for x in rows if x["ev"]["what"] == "pool")
    assert pw["ev"]["w_old"] == "acc.10k-r33" and pw["ev"]["w_new"] == "acc.10k-r35" and pw["container"] == "Контейнер А"
    assert client.get("/api/v1/farm/notify/batch/nope", headers={"Authorization": f"Bearer {h}"}).status_code == 404


def test_old_sender_signature_still_works(db):
    _devs(db)
    ns.set_prefs(db, TG, {"types": {"daily": False}})
    sent = []
    ns.tick(db, NOW, lambda tg, t: sent.append(t) or True)
    db.query(fm.FarmDevice).filter_by(ext_id="o1").one().status = "working"
    db.commit()
    assert ns.tick(db, NOW + 16 * 60, lambda tg, t: sent.append(t) or True) == 1


def test_support_quote_saved_and_forwarded(db, monkeypatch):
    out = []
    monkeypatch.setattr(sup, "managers", lambda db: [555])
    meta = {"username": "u", "quote": "🔔 Ферма · 18:29\nВернулись в работу: 1", "quote_bot": True,
            "quote_link": "https://kirkalab.ru/#ev/abc"}
    sup.incoming(db, 777, "что именно включилось?", meta, sender=lambda m, t, reply_button=False: out.append(t) or 1)
    assert "↪ В ответ на сообщение бота" in out[0] and "Вернулись в работу: 1" in out[0] and "#ev/abc" in out[0]
    th = sup.thread(db, 777)
    assert th[-1]["quote"].startswith("🔔") and th[-1]["quote_bot"] and th[-1]["quote_link"].endswith("#ev/abc")


def test_pool_noise_is_not_reported(db):
    """Пропажа данных, «было = стало» и DevFee — не смена пула; реальные смены группируются."""
    devs = _devs(db, n_err=3)
    ns.set_prefs(db, TG, {"types": {"daily": False, "pool": True, "online": False, "offline": False, "errors": False}})
    sent = []
    assert ns.tick(db, NOW, lambda tg, text, link=None: sent.append(text) or True) == 0
    a, b, c, e, f = devs[1], devs[2], devs[3], devs[4], devs[5]
    evs = [
        (a, "pool", "stratum+tcp://btc.viabtc.io:3333", ""), (a, "pool", "", "stratum+tcp://btc.viabtc.io:3333"),  # мигнул
        (b, "pool", "stratum+tcp://btcproxy.umhost.io:6060", ""),  # данные пропали
        (c, "pool", "stratum+tcp://btc.viabtc.io:3333", "DevFee"), (c, "worker", "acc.1", "venkat1812"),  # комиссия прошивки
        (e, "pool", "zec.2miners.ru:1010", "zec.2miners.com:1010"),
        (f, "pool", "zec.2miners.ru:1010", "zec.2miners.com:1010"),
    ]
    for i, (d, k, o, n) in enumerate(evs):
        db.add(fm.FarmDeviceEvent(id=2000 + i, device_id=d.id, kind=k, old=o, new=n))
    db.commit()
    assert ns.tick(db, NOW + 16 * 60, lambda tg, text, link=None: sent.append(text) or True) == 1
    t = sent[-1]
    assert "Сменили пул или воркер: 2" in t
    assert "пул zec.2miners.ru:1010 → zec.2miners.com:1010 — <b>2</b>" in t
    assert "DevFee) сейчас у 1" in t
    assert "btc.viabtc.io:3333 → btc.viabtc.io:3333" not in t and "umhost" not in t


def test_account_moves_grouped_in_one_line(db):
    devs = _devs(db, n_err=3)
    ns.set_prefs(db, TG, {"types": {"daily": False, "pool": True, "online": False, "offline": False, "errors": False}})
    sent = []
    snd = lambda tg, text, link=None: sent.append(text) or True  # noqa: E731
    assert ns.tick(db, NOW, snd) == 0
    for i, (d, po) in enumerate([(devs[1], "btc.ru.viabtc.me:3333"), (devs[2], "btcproxy.umhost.io:6060")]):
        db.add(fm.FarmDeviceEvent(id=3000 + 2 * i, device_id=d.id, kind="pool", old=po, new="btc.viabtc.io:3333"))
        db.add(fm.FarmDeviceEvent(id=3001 + 2 * i, device_id=d.id, kind="worker", old=f"EktonT21.x{i}", new=f"Ecton.y{i}"))
    db.commit()
    assert ns.tick(db, NOW + 16 * 60, snd) == 1
    assert "аккаунт EktonT21 → Ecton · на пул btc.viabtc.io:3333 — <b>2</b>" in sent[-1]
    assert "был btcproxy.umhost.io:6060" in sent[-1]
