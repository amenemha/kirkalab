"""Кошельки: распознавание адресов, фильтры спама/пыли/отравления, уведомления и API (сеть замокана)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

import pytest

from app.core.config import get_settings
from app.db import models
from app.db.wallet_models import WalletTx, WalletWatch
from app.services.wallets import chains as C
from app.services.wallets import watch as W
from tests.test_rbac_users import _create_user, _creds, _login

TRON = "TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm"
SENDER = "TDqSquXBgUCLYvYC4XZgrprLK589dkhSCf"
POISON = "TDqSxxxxxxxxxxxxxxxxxxxxxxxx9dkhSCf"[:30] + "dkhSCf"


def test_detect_all_chains():
    assert C.detect("bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh") == ["BTC"]
    assert C.detect("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == ["BTC"]
    assert C.detect("ltc1qr07zu594qf63xm7l7x6pu3a2v39m2z6hh5pp4t") == ["LTC"]
    assert C.detect("DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L") == ["DOGE"]
    assert C.detect(TRON) == ["TRON"]
    assert C.detect("TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STX") == []  # битая контрольная сумма
    assert C.detect("0x28C6c06298d514Db089934071355E5743bf21d60") == ["ETH", "BSC"]
    assert C.detect("EQBfAN7LfaUYgXZNw5Wc7GBgkEX2yhuJ5ka95J1JJwXXf4a8") == ["TON"]
    assert C.detect("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9") == ["SOL"]
    assert C.detect("привет") == [] and C.detect("") == []
    assert C.find_address(f"мой {TRON} личный") == (TRON, ["TRON"])
    assert C.find_address("https://tronscan.org/#/address/" + TRON) is None


def test_ton_and_tron_conversions():
    raw = C.ton_raw("EQBfAN7LfaUYgXZNw5Wc7GBgkEX2yhuJ5ka95J1JJwXXf4a8")
    assert raw == "0:5F00DECB7DA51881764DC3959CEC60609045F6CA1B89E646BDE49D492705D77F"
    assert C.ton_friendly(raw, True) == "EQBfAN7LfaUYgXZNw5Wc7GBgkEX2yhuJ5ka95J1JJwXXf4a8"
    assert C.tron_from_hex(C.b58check(TRON).hex()) == TRON


class FakeReader(C.Reader):
    def __init__(self, items):
        super().__init__(pause=0)
        self.items = items

    def incoming(self, chain, addr, w=None):
        return list(self.items)


def tr(uid, amt, token="USDT", frm=SENDER, flag=None, at=None):
    return C.Transfer(uid, uid, token, amt, frm, at or datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc), flag)


@pytest.fixture
def user(db):
    u = models.User(email="w@x.ru", handle="wal", hashed_password="x", telegram_user_id=4242)
    db.add(u)
    db.commit()
    return u


def test_first_poll_is_history_then_alerts(db, user, monkeypatch):
    monkeypatch.setattr(W, "prices", lambda db=None: ({"TRX": 0.33}, 90.0))
    rd = FakeReader([tr("a", 100.0)])
    [w] = W.add(db, user, TRON, "Личный", reader=rd)
    assert w.chain == "TRON" and w.label == "Личный"
    t = db.query(WalletTx).one()
    assert t.first and t.notified and t.status == "ok" and t.rub == pytest.approx(9000)
    sent = []
    rd.items = [tr("a", 100.0), tr("b", 1250.0), tr("c", 0.5), tr("d", 5, "USDT", flag="fake"), tr("e", 3, frm=POISON),
                tr("f", 0.0), tr("g", 1000, "TRX")]
    monkeypatch.setattr(C, "Reader", lambda: rd)
    n, s = W.run_wallets(db, reader=rd, sender=lambda tg, txt, kb: sent.append((tg, txt, kb)) or True,
                         now_ts=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc).timestamp())
    st = {x.uid: x.status for x in db.query(WalletTx)}
    assert st == {"a": "ok", "b": "ok", "c": "dust", "d": "fake", "e": "poison", "f": "zero", "g": "ok"}
    assert s == 2 and all(x[0] == 4242 for x in sent)
    txt = sent[0][1]
    assert "+1 250,00 USDT</b> · TRC-20" in txt and "≈ 112 500 ₽" in txt and f"<code>{SENDER}</code>" in txt and "Личный" in txt
    assert sent[0][2][0][0]["url"].startswith("https://tronscan.org/#/transaction/b")
    assert "AML" in sent[0][2][0][1]["text"]
    from app.db.activity_models import ActivityEvent
    assert db.query(ActivityEvent).filter_by(source="wallet", action="poison").count() == 1
    # повторный проход ничего не шлёт
    n, s = W.run_wallets(db, reader=rd, sender=lambda *a: sent.append(a) or True)
    assert s == 0


def test_sleep_holds_and_mute(db, user, monkeypatch):
    monkeypatch.setattr(W, "prices", lambda db=None: ({}, None))
    rd = FakeReader([])
    [w] = W.add(db, user, TRON, reader=rd)
    from app.services.notify import service as ns
    ns.set_prefs(db, 4242, {"sleep": {"on": True, "from": "23:00", "to": "07:00"}})
    rd.items = [tr("n1", 50.0)]
    night = datetime(2026, 10, 28, 0, 30, tzinfo=timezone.utc).timestamp()  # 03:30 МСК
    sent = []
    W.run_wallets(db, reader=rd, sender=lambda *a: sent.append(a) or True, now_ts=night)
    assert not sent and not db.query(WalletTx).one().notified
    W.run_wallets(db, reader=rd, sender=lambda *a: sent.append(a) or True, now_ts=night + 5 * 3600)
    assert len(sent) == 1
    w.muted = True
    rd.items = [tr("n1", 50.0), tr("n2", 70.0)]
    db.commit()
    W.run_wallets(db, reader=rd, sender=lambda *a: sent.append(a) or True, now_ts=night + 6 * 3600)
    assert len(sent) == 1


def test_limits_and_api(client, db, monkeypatch):
    monkeypatch.setattr(W, "prices", lambda db=None: ({}, None))
    monkeypatch.setattr(W, "Reader", lambda: FakeReader([]))
    monkeypatch.setattr(get_settings(), "test_mode_all_pro", False)
    email, handle = _creds("walletman")
    _create_user(client, email, handle)
    h = {"Authorization": f"Bearer {_login(client, email)}"}
    assert client.get("/api/v1/me/wallets/detect", params={"address": TRON}, headers=h).json()["chains"][0]["net"] == "TRC-20"
    r = client.post("/api/v1/me/wallets", json={"address": "nope"}, headers=h)
    assert r.status_code == 400 and "Не узнаю адрес" in r.json()["detail"]
    r = client.post("/api/v1/me/wallets", json={"address": TRON, "label": "Личный"}, headers=h)
    assert r.status_code == 200 and r.json()["used"] == 1 and r.json()["limit"] == 1
    r = client.post("/api/v1/me/wallets", json={"address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"}, headers=h)
    assert r.status_code == 400 and "1 кошельком" in r.json()["detail"]
    wid = client.get("/api/v1/me/wallets", headers=h).json()["items"][0]["id"]
    assert client.patch(f"/api/v1/me/wallets/{wid}", json={"muted": True, "min_usd": 10}, headers=h).json()["muted"]
    assert client.get(f"/api/v1/me/wallets/{wid}/tx", headers=h).json()["items"] == []
    assert client.delete(f"/api/v1/me/wallets/{wid}", headers=h).status_code == 200
    assert db.query(WalletWatch).count() == 0


def test_evm_address_watches_both_networks(db, user, monkeypatch):
    monkeypatch.setattr(W, "prices", lambda db=None: ({}, None))
    ws = W.add(db, user, "0x28C6c06298d514Db089934071355E5743bf21d60", reader=FakeReader([]))
    assert [w.chain for w in ws] == ["ETH", "BSC"] and ws[0].address == "0x28c6c06298d514db089934071355e5743bf21d60"
    assert len(W.add(db, user, "0x28C6c06298d514Db089934071355E5743bf21d60", chains=["BSC"], reader=FakeReader([]))) == 1


def test_internal_bot(client, db, monkeypatch, user):
    monkeypatch.setattr(W, "prices", lambda db=None: ({}, None))
    monkeypatch.setattr(W, "Reader", lambda: FakeReader([]))
    monkeypatch.setattr(get_settings(), "bot_internal_secret", "sek")
    h = {"X-Bot-Secret": "sek"}
    d = client.post("/api/v1/internal/wallets", json={"action": "detect", "text": f"вот {TRON} мой"}, headers=h).json()
    assert d["address"] == TRON and d["chains"][0]["id"] == "TRON"
    r = client.post("/api/v1/internal/wallets", json={"action": "add", "tg_id": 4242, "address": TRON, "label": "Личный"}, headers=h).json()
    assert r["ok"] and r["added"][0]["net"] == "TRC-20"
    lst = client.post("/api/v1/internal/wallets", json={"action": "list", "tg_id": 4242}, headers=h).json()
    assert lst["items"][0]["label"] == "Личный" and lst["items"][0]["last"] is None
    wid = lst["items"][0]["id"]
    assert client.post("/api/v1/internal/wallets", json={"action": "mute", "tg_id": 4242, "id": wid}, headers=h).json()["ok"]
    assert db.get(WalletWatch, wid).muted


def test_bsc_reader_parses_logs():
    class R(C.Reader):
        def rpc(self, url, method, params):
            if method == "eth_blockNumber":
                return hex(1000)
            if method == "eth_getBalance":
                return hex(int(0.5e18))
            if params[0]["topics"][2] is None:  # исходящие
                return []
            return [{"address": "0x55d398326f99059ff775485246999027b3197955", "data": hex(int(12.5e18)), "transactionHash": "0xabc",
                     "logIndex": "0x1", "topics": [C.Reader.TRANSFER, "0x" + "0" * 24 + "11" * 20, params[0]["topics"][2]],
                     "blockTimestamp": hex(1793196000)}]
    w = WalletWatch(chain="BSC", address="0x" + "22" * 20, balance={"BNB": 0.2}, cursor="900")
    out = R(pause=0).bsc(w.address, w)
    assert out[0].token == "USDT" and out[0].amount == 12.5 and out[0].frm == "0x" + "11" * 20
    assert out[1].token == "BNB" and out[1].amount == pytest.approx(0.3) and w.cursor == "1000"


def test_outgoing_is_stored_and_off_by_default(db, user, monkeypatch):
    monkeypatch.setattr(W, "prices", lambda db=None: ({}, None))
    rd = FakeReader([])
    [w] = W.add(db, user, TRON, "Транзит", reader=rd)
    assert w.outgoing is False
    out = C.Transfer("o1:out", "o1", "USDT", 480.0, TRON, datetime(2026, 10, 28, 11, 0, tzinfo=timezone.utc), dir="out", to=SENDER)
    rd.items = [out]
    sent = []
    W.run_wallets(db, reader=rd, sender=lambda *a: sent.append(a) or True)
    t = db.query(WalletTx).one()
    assert t.direction == "out" and t.to_addr == SENDER and t.notified and not sent  # выключено — не шлём
    w.outgoing = True
    db.commit()
    rd.items = [out, C.Transfer("o2:out", "o2", "USDT", 15.0, TRON, datetime(2026, 10, 28, 11, 5, tzinfo=timezone.utc), dir="out", to=SENDER)]
    W.run_wallets(db, reader=rd, sender=lambda *a: sent.append(a) or True)
    assert len(sent) == 1
    txt = sent[0][1]
    assert "🔴 <b>−15,00 USDT</b>" in txt and f"Куда: <code>{SENDER}</code>" in txt and "С кошелька: Транзит" in txt
    v = W.view(w, db)
    assert v["outgoing"] and v["incoming"] == 0 and v["outgoing_n"] == 2
    h = W.history(db, w)
    assert h[0]["dir"] == "out" and h[0]["to"] == SENDER
    r = W.alert_rich(w, db.query(WalletTx).filter_by(uid="o2:out").one(), db, "✅ не в чёрном списке эмитента")
    assert "<h3>🔴 −15,00 USDT · TRC-20</h3>" in r and "<b>Транзит</b>" in r and f"<code>{TRON}</code>" in r and "Ушло" in r
    assert "…" not in r  # адреса целиком
    assert 'type="copy_text"' in r and f'data="wal:mute:{w.id}"' in r


def test_rate_line():
    assert W.rate_line("USDT", "TRX", {"TRX": 0.28}, 81.0) == "1 USDT = 81,00 ₽ · 1 TRX = 22,68 ₽"
    assert W.rate_line("BTC", "BTC", {"BTC": 100000}, 80.0) == "1 BTC = 8 000 000 ₽"
    assert W.rate_line("BTC", "BTC", {}, None) is None


def test_recent_before_add_is_alerted(db, user, monkeypatch):
    """Перевод за 10 минут до добавления адреса — не молчим (случай с DOGE 29.10)."""
    monkeypatch.setattr(W, "prices", lambda db=None: ({"DOGE": 0.2}, 80.0))
    now = datetime.now(timezone.utc)
    old = C.Transfer("old", "old", "DOGE", 50.0, None, now - timedelta(days=3))
    fresh = C.Transfer("fr", "fr", "DOGE", 1000.0, None, now - timedelta(minutes=11))
    [w] = W.add(db, user, "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L", reader=FakeReader([old, fresh]))
    st = {t.uid: t.notified for t in db.query(WalletTx)}
    assert st == {"old": True, "fr": False}
    sent = []
    W.run_wallets(db, reader=FakeReader([old, fresh]), sender=lambda *a: sent.append(a) or True)
    assert len(sent) == 1 and "до начала наблюдения" in sent[0][1] and "+1 000 DOGE" in sent[0][1]


def test_transient_error_hidden_until_stale(db, user):
    w = WalletWatch(user_id=user.id, chain="TRON", address=TRON, last_error="сеть не ответила: ReadTimeout",
                    last_ok=datetime.now(timezone.utc) - timedelta(minutes=5))
    assert W.shown_error(w) is None
    w.last_ok = datetime.now(timezone.utc) - timedelta(minutes=40)
    assert W.shown_error(w) == "сеть не ответила: ReadTimeout"


def test_reader_retries_and_cooldown():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "retry.example" in str(req.url) and len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        if "busy.example" in str(req.url):
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": 1})
    rd = C.Reader(http=httpx.Client(transport=httpx.MockTransport(handler)), pause=0)
    assert rd.get("https://retry.example/x") == {"ok": 1} and len(calls) == 2
    with pytest.raises(C.ChainError):
        rd.get("https://busy.example/x")
    n = len(calls)
    with pytest.raises(C.ChainError):  # хост «отдыхает» — даже не спрашиваем
        rd.get("https://busy.example/y")
    assert len(calls) == n
    C._COOL.clear()


def test_esplora_and_doge_directions():
    me, other = "bc1qme", "bc1qother"

    class R(C.Reader):
        def get(self, url, **params):
            if "blockchair" in url:
                raise C.ChainError("источник ответил 430")
            if "blockcypher" in url:
                return {"txrefs": [{"tx_hash": "d1", "tx_input_n": -1, "value": 1000_0000_0000, "confirmations": 5, "confirmed": "2026-10-29T16:09:12Z"},
                                   {"tx_hash": "d2", "tx_input_n": 0, "value": 1000_0000_0000, "confirmations": 3, "confirmed": "2026-10-29T16:30:22Z"}]}
            return [{"txid": "in1", "status": {"confirmed": True, "block_time": 1793196000},
                     "vin": [{"prevout": {"scriptpubkey_address": other}}], "vout": [{"scriptpubkey_address": me, "value": 5000}]},
                    {"txid": "out1", "status": {"confirmed": True, "block_time": 1793197000},
                     "vin": [{"prevout": {"scriptpubkey_address": me}}],
                     "vout": [{"scriptpubkey_address": other, "value": 3000}, {"scriptpubkey_address": me, "value": 1500}]}]
    r = R(pause=0)
    e = r.esplora("x", "BTC", me)
    assert [(t.dir, t.amount, t.to) for t in e] == [("in", 5e-05, me), ("out", 3e-05, other)]
    d = sorted(r.doge("DADDR"), key=lambda t: t.at)
    assert [(t.uid, t.dir, t.amount) for t in d] == [("d1", "in", 1000.0), ("d2:out", "out", 1000.0)]


def test_tron_reader_directions():
    me = TRON

    class R(C.Reader):
        def get(self, url, **params):
            assert "only_to" not in params
            if url.endswith("trc20"):
                return {"data": [
                    {"transaction_id": "t1", "to": me, "from": SENDER, "value": "1250000000", "type": "Transfer", "block_timestamp": 1793196000000,
                     "token_info": {"address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "symbol": "USDT", "decimals": 6}},
                    {"transaction_id": "t2", "to": SENDER, "from": me, "value": "480000000", "type": "Transfer", "block_timestamp": 1793197000000,
                     "token_info": {"address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "symbol": "USDT", "decimals": 6}}]}
            return {"data": []}
    out = R(pause=0).tron(me)
    assert [(t.uid, t.dir, t.amount, t.to) for t in out] == [
        ("t1:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "in", 1250.0, me), ("t2:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t:out", "out", 480.0, SENDER)]
