"""Кошельки: распознавание адресов, фильтры спама/пыли/отравления, уведомления и API (сеть замокана)."""
from __future__ import annotations

from datetime import datetime, timezone

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
            return [{"address": "0x55d398326f99059ff775485246999027b3197955", "data": hex(int(12.5e18)), "transactionHash": "0xabc",
                     "logIndex": "0x1", "topics": [C.Reader.TRANSFER, "0x" + "0" * 24 + "11" * 20, "0x"], "blockTimestamp": hex(1793196000)}]
    w = WalletWatch(chain="BSC", address="0x" + "22" * 20, balance={"BNB": 0.2}, cursor="900")
    out = R(pause=0).bsc(w.address, w)
    assert out[0].token == "USDT" and out[0].amount == 12.5 and out[0].frm == "0x" + "11" * 20
    assert out[1].token == "BNB" and out[1].amount == pytest.approx(0.3) and w.cursor == "1000"
