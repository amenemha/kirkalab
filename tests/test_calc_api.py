from decimal import Decimal

import pytest

from app.services.market import service as market_service
from app.services.market.provider import RawMarketData

BOT_SECRET = "test-bot-secret"


@pytest.fixture(autouse=True)
def _seed_market(monkeypatch):
    market_service.reset_cache()

    def _raw():
        return RawMarketData(
            price_usdt=Decimal("60000"),
            network_difficulty=Decimal("80000000000000"),
            block_reward_btc=Decimal("3.125"),
        )

    monkeypatch.setattr(market_service, "fetch_market_data", _raw)
    yield
    market_service.reset_cache()


def _payload(**overrides):
    data = {
        "hashrate_ths": "100",
        "power_w": 3250,
        "quantity": 1,
        "power_price": "0.05",
        "hardware_cost": "2000",
    }
    data.update(overrides)
    return data


def test_calc_endpoint_returns_expected(client):
    resp = client.post("/api/v1/calc/", json=_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["btc_per_day"]) == pytest.approx(7.858034e-5, rel=1e-5)
    assert float(body["net_profit_day"]) == pytest.approx(0.767672, rel=1e-5)
    assert float(body["roi_days"]) == pytest.approx(2605.278, rel=1e-4)
    assert body["btc_price_usdt"] == "60000"
    assert body["market_captured_at"]


def test_calc_endpoint_rejects_free_overrange(client):
    resp = client.post("/api/v1/calc/", json=_payload(quantity=6))
    assert resp.status_code == 422, resp.text
    assert "quantity" in resp.text


def test_calc_endpoint_503_when_market_unavailable(client, monkeypatch):
    from app.services.market.provider import MarketFetchError

    def _boom():
        raise MarketFetchError("down")

    monkeypatch.setattr(market_service, "fetch_market_data", _boom)
    market_service.reset_cache()

    resp = client.post("/api/v1/calc/", json=_payload())
    assert resp.status_code == 503, resp.text


def test_refresh_market_requires_bot_secret(client):
    resp = client.post("/api/v1/internal/refresh-market")
    assert resp.status_code == 403

    resp = client.post(
        "/api/v1/internal/refresh-market", headers={"X-Bot-Secret": BOT_SECRET}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["btc_price_usdt"] == "60000"
