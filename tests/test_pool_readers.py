"""Readers of other pools: parse links and normalize answers (HTTP is mocked)."""
from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.services.pools import readers as R


def client(routes: dict):
    def h(req: httpx.Request):
        for k, v in routes.items():
            if k in str(req.url):
                if isinstance(v, int):
                    return httpx.Response(v, text="nope")
                return httpx.Response(200, text=json.dumps(v))
        return httpx.Response(404, text="nf")
    return httpx.Client(transport=httpx.MockTransport(h))


def test_units():
    assert R.hsu("225.18 TH/s") == pytest.approx(225.18e12)
    assert R.hsu("432.93", "PH/s") == pytest.approx(432.93e15)
    assert R.hsu("2113.73 Thash/s") == pytest.approx(2113.73e12)
    assert R.hsu("534.48 MH/s") == pytest.approx(534.48e6)
    assert R.pct("0.16%") == pytest.approx(0.0016)
    assert R.pct("0.0102") == pytest.approx(0.0102)
    h = R.hour_avg([(7200, 10.0, 0.0), (9000, 20.0, 0.02), (10800, 5.0, None)])
    assert h[7200][0] == 15.0 and h[7200][2] == pytest.approx(0.01) and h[10800][0] == 5.0


def test_parse_links():
    assert R.Antpool().parse("https://v3.antpool.com/observer?accessKey=AK1&coinType=BTC&observerUserId=Acc1") == \
        {"key": "AK1:Acc1", "coin": "BTC", "account": "Acc1"}
    p = R.F2Pool().parse("https://www.f2pool.com/mining-user-kas/da9b0c3804359235dea8ca2fe57e51d9?user_name=tref47")
    assert p == {"key": "da9b0c3804359235dea8ca2fe57e51d9", "coin": "KAS", "account": "tref47"}
    assert R.F2Pool().parse("https://www.f2pool.com/mining-user/053fd0cf0181d333ff0d4ebb3122d666")["coin"] == "BTC"
    assert R.EMCD().parse("https://emcd.io/pool/watcher/ru/fbd15ea9-bd60-49da-b825-0b7cefd54ecd%20")["key"] == "fbd15ea9-bd60-49da-b825-0b7cefd54ecd"
    assert R.Binance().parse("https://pool.binance.com/ru/statistics?urlParams=Kw5SZF114j")["key"] == "Kw5SZF114j"
    assert R.TwoMiners().parse("https://kas.2miners.com/account/kaspa:qpm86")["coin"] == "KAS"
    assert R.BitFuFu().parse("https://us-miner-service.bitfufu.net/?access_key=abc&miner_prefix=P-1")["key"] == "abc:P-1"
    assert R.Antpool().parse("https://www.antpool.com/home") is None


def test_antpool_read():
    ok = {"code": "000000"}
    c = client({
        "hash/query": {**ok, "data": {"userId": "Acc1", "hsNow": "432.93", "hsNowUnit": "PH/s", "hsLast1D": "435", "hsLast1DUnit": "PH/s",
                                      "onlineWorkerNum": 2, "offlineWorkerNum": 1}},
        "user/speed": {**ok, "data": {"graphContractPointList": [{"hashRate": 400, "hashRateUnit": "PH/s", "rejectRatio": 0.001, "timestamp": 1793034000000}]}},
        "earnings/query": {**ok, "data": {"earningsYesterday": "0.19", "earningsNoPay": "0.1", "earningsTotal": "174", "startTime": 1793059200000.0}},
        "worker/list": {**ok, "data": {"items": [{"workerId": "W1", "userWorkerId": "Acc1.W1", "workerStatus": 1, "hsLast10Min": "225 TH/s",
                                                  "hsLast1H": "220 TH/s", "hsLast1D": "230 TH/s", "rejectRatio": "0.08%", "shareLastTime": 1793195108000}]}},
    })
    s = R.Antpool(http=c, pause=0).read("", "AK1:Acc1", "BTC")
    assert s.hs_10m == pytest.approx(432.93e15) and s.workers_active == 2 and s.profit_24h == 0.19 and s.balance == 0.1
    assert s.workers[0]["status"] == "active" and s.workers[0]["reject"] == pytest.approx(0.0008)
    assert 1793034000 in s.hourly


def test_antpool_dead_link():
    c = client({"hash/query": {"code": "E1", "msg": "ERROR_USER_NOT_EXIST"}})
    with pytest.raises(R.PoolError, match="наблюдатель удалён"):
        R.Antpool(http=c, pause=0).read("", "AK:U", "BTC")


def test_binance_read():
    c = client({
        "authorize": {"code": 0, "success": True, "data": {"userHash": {"userName": "ttnmain"}, "coinName": {"name": "LTC"}}},
        "stat": {"code": 0, "data": {"h15m": "954.44 MH/s", "h1d": "1.05 GH/s", "validNum": 1, "invalidNum": 0,
                                     "profitYesterday": {"LTC": "0.0014"}, "profitToday": {"LTC": "0.0008"},
                                     "hoursList": [{"time": 1793106000000, "hash": "925.8 MH/s", "reject": "0.0102", "validNum": 1}],
                                     "dayList": [{"time": 1793059200000, "total": "1060220631"}]}},
        "miner/index": {"code": 0, "data": {"pageSize": 20, "totalNum": 1, "workerDatas": [
            {"workerName": "DG.HOME1", "h15m": "954.44 MH/s", "h1d": "1.05 GH/s", "status": 1, "rejectRate": "0%", "lastShareTime": 1793195234000}]}},
    })
    s = R.Binance(http=c, pause=0).read("", "TOK", None)
    assert s.coin == "LTC" and s.account == "ttnmain" and s.profit_24h == pytest.approx(0.0014)
    assert s.hs_1h == pytest.approx(925.8e6) and s.days[date(2026, 10, 27)]["hs"] == pytest.approx(1060220631)
    assert s.workers[0]["full"] == "ttnmain.DG.HOME1"


def test_emcd_read_picks_coin():
    c = client({
        "/info": {"username": "u1", "coins": {"btc": {"balance": 0, "total_reward": 0}, "ltc": {"balance": 1, "total_reward": 5},
                                              "doge": {"total_reward": 999}}},
        "ltc/workers": {"details": [{"worker": "a1", "user": "u1", "status": 1, "hashrate": 1e9, "hashrate1h": 1e9, "hashrate24h": 9e8}],
                        "total_count": {"active": 1, "inactive": 0, "dead_count": 0},
                        "total_hashrate": {"hashrate": 1e9, "hashrate1h": 1e9, "hashrate24h": 9e8}},
        "ltc/hashrate": {"data": {"hrs": {"week": {"tsHrs": {"1792587600": 9e8}}}}},
        "ltc/income": {"income": [{"timestamp": 1772347436.9, "income": 0.01, "type": "mining", "total_hashrate": 9e8}]},
    })
    s = R.EMCD(http=c, pause=0).read("", "uuid", None)
    assert s.coin == "LTC" and s.balance == 1 and s.workers[0]["full"] == "u1.a1" and s.profit_24h == 0.01


def test_f2pool_read():
    c = client({
        "load_by_duration": {"status": "ok", "data": {"hashrate_history": {"key": "BTC (TH/s)", "values": [[1793115000000, 10.0, "btc"], [1793116800000, 20.0, "btc"]]},
                                                       "hashrate_history_stale_rate": {"values": [[1793115000000, 0.1]]}}},
        "get_pagination_workers": {"recordsTotal": 1, "originData": {"summary": {"hash_rate_daily": "253508287618380.22"},
                                                                     "worker_length_online": 0, "worker_length_offline": 1},
                                   "data": [{"name": "w1", "hashrate": "0.00", "hashrate_last_day": "0.10", "hashes_last_day": 8640000000000000,
                                             "status": 1, "last_share": 1793165722, "stalerate_last_day": "0.00", "online_time_7d": 4.6}]},
        "load_payout_history_income": {"status": "ok", "data": {"income_data": [{"created_at": 1793145600, "amount": 0.0009, "hash_rate": "2113.73 Thash/s", "currency_code": "btc"}]}},
    })
    s = R.F2Pool(http=c, pause=0).read("https://www.f2pool.com/mining-user/abcdef0123456789abcdef?user_name=acc", "abcdef0123456789abcdef", "BTC")
    assert s.account == "acc" and s.hs_1d == pytest.approx(253508287618380.22) and s.workers_inactive == 1
    assert s.workers[0]["status"] == "unactive" and s.workers[0]["hs_1d"] == pytest.approx(1e11)
    assert s.hourly[1793113200][0] == pytest.approx(10e12) and s.hourly[1793116800][0] == pytest.approx(20e12)
    assert s.days[date(2026, 10, 27)]["profit"] == pytest.approx(0.0009)


def test_2miners_404():
    with pytest.raises(R.PoolError, match="не открывается"):
        R.TwoMiners(http=client({}), pause=0).read("", "addr", "ZEN")


def test_apply_snap_and_relink(db):
    from app.db import pool_models as pm
    from app.services.pools import sync

    a = pm.PoolAccount(provider="antpool", access_key="x" * 40, coin="", sub_user_id=0, active=False,
                       url="https://v3.antpool.com/observer?accessKey=AK&coinType=BTC&observerUserId=Acc")
    b = pm.PoolAccount(provider="trustpool", access_key="y" * 40, coin="", sub_user_id=0, active=False, url="https://trustpool.ru/")
    db.add_all([a, b])
    db.commit()
    sync.relink(db)
    db.refresh(a)
    db.refresh(b)
    assert a.access_key == "AK:Acc" and a.coin == "BTC" and a.active and a.account == "Acc"
    assert b.coin == "?" and "нет ключа" in b.last_error
    s = R.Snap(account="Acc", hs_10m=5e15, hs_1d=4e15, workers_active=3, hourly={1000 * 3600: (4e15, 3, 0.001)},
               days={date(2026, 10, 27): {"hs": 4e15, "profit": 0.1}},
               workers=[{"name": "W1", "full": "Acc.W1", "status": "active", "hs_10m": 1e14}])
    sync.apply_snap(db, a, s)
    db.commit()
    assert a.hs_1h == 5e15 and a.last_ok is not None
    assert db.get(pm.PoolDaily, (a.id, date(2026, 10, 27))).profit == 0.1
    assert db.query(pm.PoolWorker).filter_by(account_id=a.id).one().full_name == "acc.w1"
    assert db.get(pm.PoolHourly, (a.id, 3600000)).hs == 4e15
