import io
from datetime import date, datetime, timedelta, timezone

import openpyxl

from app.db import pool_models as pm
from app.services.pools.daily import day_table
from app.services.pools.sync import import_other, import_rows, rows_from_xlsx, run_pools
from app.services.pools.viabtc import hs, parse_key
from tests.test_rbac_users import _make_admin

KEY = "030a4d9fa1af553df10bc85e8981a9e8"


class FakeVia:
    def __init__(self, start):
        self.start = start

    def info(self, key):
        if key.startswith("bad"):
            raise RuntimeError("4060 Invalid access key")
        return {"account": "BBWTechnology", "accounts": [{"user_id": 1, "account": "BBWTechnology"}, {"user_id": 2, "account": "BBWTechnology2"}]}

    def home(self, key, coin, user_id=0):
        return {"hashrate_10min": "19.966P", "hashrate_1hour": "19.829P", "hashrate_1day": "1.932P", "total_active": 49,
                "total_unactive": 0, "total_invalid": 2, "account_balance": "0.0219", "profit_24hour": "0.00065", "profit_total": "5.1"}

    def hourly(self, key, coin, user_id=0):
        return {self.start + i * 3600: (100e12 + i * 1e12, 2, 0.01) for i in range(72)}

    def profit_days(self, key, coin, user_id=0):
        return [{"date": "2026-10-22", "hashrate": "6240658593020593", "total_profit": "0.00273703", "pps_profit": "0.0026",
                 "pplns_profit": "0.00005", "pps_plus_rate": "0.98", "unit_output": "0.00000043", "price": "82479.18", "total_money": "225.74"}]

    def workers(self, key, coin, user_id=0):
        return [{"user": "bbwtechnology", "name": "100x7x46", "status": "active", "hashrate_10min": "600479950316066",
                 "hashrate_1hour": "387047639850534", "hashrate_1day": "15866360015336", "reject_rate_1hour": "0.0133",
                 "online_time_7d": "149.25", "last_active": 1792777253}]


def test_hs_and_key():
    assert hs("11.722P") == 11.722e15
    assert hs("300.240T") == 300.24e12
    assert hs("600479950316066") == 600479950316066.0
    assert hs(None) is None
    assert parse_key(f"https://www.viabtc.com/ru/observer/dashboard?access_key={KEY}&coin=BTC") == KEY


def _xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Диагональ_VIABTC"
    ws.append(["отслеживать", "url", "coin", "user_name", "telegram_target", "thread_id", "hashrate_threshold"])
    ws.append(["да", f"https://www.viabtc.com/observer/dashboard?access_key={KEY}", "BTC", "BBWTechnology2", -1002600579685, 1049, 0])
    ws.append(["нет", "https://www.viabtc.com/observer/dashboard?access_key=badbadbadbadbadbad", "LTC", "x", None, None, None])
    o = wb.create_sheet("Другие пулы")
    o.append(["Гексакон"])
    o.append(["https://www.f2pool.com/mining-user-kas/da9b0c"])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_import_and_sync(db):
    rows, other = rows_from_xlsx(_xlsx())
    assert len(rows) == 2 and rows[0]["track"] and rows[0]["chat"] == "-1002600579685"
    assert other == [{"group": "Гексакон", "url": "https://www.f2pool.com/mining-user-kas/da9b0c"}]
    now = int(datetime.now(timezone.utc).timestamp()) // 3600 * 3600
    fake = FakeVia(now - 71 * 3600)
    res = import_rows(db, rows, fake)
    assert res == {"added": 1, "updated": 0, "invalid": 1}
    assert import_other(db, other) == 1
    a = db.query(pm.PoolAccount).filter_by(provider="viabtc").one()
    assert a.sub_user_id == 2 and a.account == "BBWTechnology2"
    ok, bad = run_pools(db, fake)
    assert (ok, bad) == (1, 0)
    db.refresh(a)
    assert a.hs_10m == 19.966e15 and a.workers_active == 49
    assert db.query(pm.PoolHourly).count() == 71  # the current hour is skipped
    assert db.query(pm.PoolWorker).one().full_name == "bbwtechnology.100x7x46"
    assert db.query(pm.PoolDaily).one().money_usd == 225.74
    # re-run is idempotent
    run_pools(db, fake)
    assert db.query(pm.PoolHourly).count() == 71


def test_day_table_msk(db):
    a = pm.PoolAccount(provider="viabtc", access_key="k", coin="BTC", sub_user_id=0)
    db.add(a)
    db.commit()
    d = date(2026, 10, 1)
    start = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()) - 3 * 3600  # 00:00 MSK
    for i in range(24):
        db.add(pm.PoolHourly(account_id=a.id, ts=start + i * 3600, hs=200e12))
    db.add(pm.PoolDaily(account_id=a.id, day=d + timedelta(days=1), hs=150e12, profit=0.001))
    db.commit()
    rows = day_table(db, a.id, d, d + timedelta(days=2))
    assert rows[0]["avg_hs"] == 200e12 and rows[0]["hours"] == 24
    assert rows[1]["avg_hs"] == 150e12 and rows[1]["source"] == "суточное значение пула"
    assert rows[2]["avg_hs"] is None


def test_api(client, db):
    token, _ = _make_admin(client, db)
    h = {"Authorization": f"Bearer {token}"}
    a = pm.PoolAccount(provider="viabtc", access_key="k", coin="BTC", sub_user_id=0, account="acc", group_name="G")
    db.add(a)
    db.commit()
    r = client.get("/api/v1/pools/accounts", headers=h)
    assert r.status_code == 200 and r.json()["items"][0]["account"] == "acc"
    r = client.get(f"/api/v1/pools/accounts/{a.id}/daily?d_from=2026-10-01&d_to=2026-10-03", headers=h)
    assert r.status_code == 200 and len(r.json()["rows"]) == 3
    r = client.get("/api/v1/pools/daily/export.xlsx?d_from=2026-10-01&d_to=2026-10-03", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert client.get("/api/v1/pools/accounts").status_code in (401, 403)
