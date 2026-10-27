"""Расчёт не-SHA-256 моделей (Scrypt и др.), цена э/э в рублях, тестовый режим."""
from decimal import Decimal

import pytest

from app.db import models
from app.services.calc.core import MarketData, MiningInput, calculate
from app.services.market import algos
from app.services.market import service as market_service
from app.services.market.provider import RawMarketData

BOT_SECRET = "test-bot-secret"
TG = 777001

WTM = {
    "Bitcoin": {"tag": "BTC", "nethash": 9e20, "block_time": 600, "block_reward": 3.125, "exchange_rate": 100000},
    "Litecoin": {"tag": "LTC", "nethash": 2.8e15, "block_time": 150, "block_reward": 6.25, "exchange_rate": 0.001},
    "Dogecoin": {"tag": "DOGE", "nethash": 2.7e15, "block_time": 60, "block_reward": 10000, "exchange_rate": 0.000002},
    "Kaspa": {"tag": "KAS", "nethash": 6e17, "block_time": 0.1, "block_reward": 3.5, "exchange_rate": 0.0000008},
}


@pytest.fixture(autouse=True)
def _wtm(monkeypatch):
    algos.reset_cache()
    market_service.reset_cache()
    monkeypatch.setattr(algos, "_fetch", lambda: WTM)
    monkeypatch.setattr(market_service, "fetch_market_data", lambda: RawMarketData(
        price_usdt=Decimal("100000"), network_difficulty=Decimal("150000000000000"), block_reward_btc=Decimal("3.125")))
    yield
    algos.reset_cache()
    market_service.reset_cache()


def test_scrypt_merges_ltc_and_doge():
    am = algos.algo_market("Scrypt")
    tags = [t for t, _, _ in am.coins_per_hs_day]
    assert tags == ["LTC", "DOGE"]
    # 16 GH/s × usd_per_hs_day — разумный порядок (единицы долларов в сутки).
    usd = am.usd_per_hs_day * Decimal(16) * Decimal(10) ** 9
    assert Decimal(1) < usd < Decimal(100)


def test_obsolete_and_unknown_algos_readable():
    with pytest.raises(ValueError, match="Decred"):
        algos.algo_market("Blake256R14")
    with pytest.raises(ValueError, match="не поддерживается"):
        algos.algo_market("Nope")


def test_unit_multipliers():
    assert algos.unit_mult("MH/s") == Decimal(10) ** 6
    assert algos.unit_mult("Ksol/s") == Decimal(1000)
    assert algos.unit_mult(None) == Decimal(10) ** 12


def test_core_generic_income_path():
    m = MarketData(btc_price_usdt=Decimal(100000), network_difficulty=Decimal(1), block_reward_btc=Decimal(0),
                   usd_per_hs_day=Decimal("1e-9"))
    r = calculate(MiningInput(hashrate_ths=Decimal(16000), power_w=3400, quantity=1, power_price=Decimal("0.05"),
                              pool_fee_pct=Decimal(0), hash_mult=Decimal(10) ** 6), m)
    assert r.gross_revenue_usdt_day == Decimal("16.000")  # 16e9 H/s × 1e-9
    assert r.btc_per_day > 0


def _model(db, **kw):
    m = models.DeviceModel(brand="Bitmain", model_name=kw.pop("name"), default_hashrate_ths=Decimal(kw.pop("hr")),
                           default_power_w=kw.pop("w"), **kw)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_internal_calc_scrypt_model(client, db):
    m = _model(db, name="Antminer L9", hr="16000", w=3360, algorithm="Scrypt", hashrate_unit="MH/s")
    r = client.post("/api/v1/internal/calc", headers={"X-Bot-Secret": BOT_SECRET},
                    json={"telegram_user_id": TG, "device_model_id": m.id, "quantity": 1, "power_price": "0.05"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["algorithm"] == "Scrypt"
    assert [c["coin"] for c in res["coins"]] == ["LTC", "DOGE"]
    assert Decimal(res["gross_revenue_usdt_day"]) > 0


def test_internal_calc_sha_model_large_hashrate_ok(client, db):
    m = _model(db, name="Antminer S21 Hyd", hr="335", w=5360, algorithm="SHA-256", hashrate_unit="TH/s")
    r = client.post("/api/v1/internal/calc", headers={"X-Bot-Secret": BOT_SECRET},
                    json={"telegram_user_id": TG, "device_model_id": m.id, "quantity": 3, "power_price": "0.05"})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["coins"] is None


def test_power_price_in_rubles_converted(client, db, monkeypatch):
    from app.services.fx import service as fx_service

    monkeypatch.setattr(fx_service, "get_anchor_rate", lambda db, code: Decimal("80"))
    m = _model(db, name="Antminer S19", hr="95", w=3250, algorithm="SHA-256", hashrate_unit="TH/s")
    r = client.post("/api/v1/internal/calc", headers={"X-Bot-Secret": BOT_SECRET},
                    json={"telegram_user_id": TG, "device_model_id": m.id, "quantity": 1, "power_price": "5.6",
                          "power_price_currency": "RUB"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["result"]["input"]["power_price"]) == Decimal("0.07")


def test_test_mode_makes_everyone_pro(monkeypatch):
    from app.core.config import get_settings
    from app.services.billing.entitlement import is_pro

    class U:
        is_pro = False
        premium_until = None

    monkeypatch.setattr(get_settings(), "test_mode_all_pro", True)
    assert is_pro(U()) is True
    monkeypatch.setattr(get_settings(), "test_mode_all_pro", False)
    assert is_pro(U()) is False


def test_difficulty_fallback(monkeypatch):
    from app.services.market import provider

    calls = []

    def fake_get_json(client, url, retries):
        calls.append(url)
        if "difficulty-adjustment" in url:
            return {"progressPercent": 50}
        if "hashrate/3d" in url:
            return {"currentDifficulty": 1.5e14}
        return {"bitcoin": {"usd": 100000}}

    monkeypatch.setattr(provider, "_get_json", fake_get_json)
    out = provider.fetch_difficulty_and_reward(None, 1)
    assert out[0] == Decimal("1.5E+14")
    assert any("hashrate/3d" in u for u in calls)


def test_site_calc_by_model_rub(client, db, monkeypatch):
    from app.services.fx import service as fx_service

    monkeypatch.setattr(fx_service, "get_anchor_rate", lambda db, code: Decimal("80"))
    m = _model(db, name="Antminer L9", hr="16000", w=3360, algorithm="Scrypt", hashrate_unit="MH/s")
    r = client.post("/api/v1/calc/model", json={"device_model_id": m.id, "quantity": 2, "power_price": "5.5",
                                                 "power_price_currency": "RUB", "hardware_cost": "400000"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["currency"] == "RUB" and b["coins"][0]["coin"] == "LTC"
    assert Decimal(b["local"]["power_cost_day"]) == (Decimal("3.36") * 24 * 2 * Decimal("5.5")).quantize(Decimal("0.01"))
    assert client.post("/api/v1/calc/model", json={"device_model_id": 999999}).status_code == 404
