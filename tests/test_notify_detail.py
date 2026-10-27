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
    assert "Смена воркера: 1, пула: 1" in text
    assert "было: <code>acc.10k-r33</code>" in text and "стало: <code>acc.10k-r35</code>" in text
    assert "btc.viabtc.io:3333 → btc.f2pool.com:1314" in text and "<blockquote>" in text
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
    meta = {"username": "u", "quote": "🔔 Kirka · ферма · 18:29\nВернулись в работу: 1", "quote_bot": True,
            "quote_link": "https://kirkalab.ru/#ev/abc"}
    sup.incoming(db, 777, "что именно включилось?", meta, sender=lambda m, t, reply_button=False: out.append(t) or 1)
    assert "↪ В ответ на сообщение бота" in out[0] and "Вернулись в работу: 1" in out[0] and "#ev/abc" in out[0]
    th = sup.thread(db, 777)
    assert th[-1]["quote"].startswith("🔔") and th[-1]["quote_bot"] and th[-1]["quote_link"].endswith("#ev/abc")
