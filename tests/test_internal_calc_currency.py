"""The internal calc endpoint surfaces a local-currency view (Queue 3).

The USDT economics stay authoritative; ``local`` is an additive presentation
layer converted at the FX rate. Market + FX upstreams and Redis are mocked.
"""
from decimal import Decimal

import pytest

from app.services.fx import service as fx_service
from app.services.market import service as market_service
from app.services.market.provider import RawMarketData

BOT_SECRET = "test-bot-secret"
TG_ID = 777001


@pytest.fixture(autouse=True)
def _mock_upstreams(monkeypatch):
    market_service.reset_cache()
    monkeypatch.setattr(
        market_service,
        "fetch_market_data",
        lambda: RawMarketData(
            price_usdt=Decimal("60000"),
            network_difficulty=Decimal("80000000000000"),
            block_reward_btc=Decimal("3.125"),
        ),
    )
    monkeypatch.setattr(fx_service, "_redis_client", lambda: None)
    monkeypatch.setattr(
        fx_service,
        "fetch_anchor_rates",
        lambda fiats: {"RUB": Decimal("90.0"), "USD": Decimal("1.0")},
    )
    yield
    market_service.reset_cache()


def _headers():
    return {"X-Bot-Secret": BOT_SECRET}


def _payload(**overrides):
    data = {
        "telegram_user_id": TG_ID,
        "hashrate_ths": "100",
        "power_w": 3250,
        "quantity": 1,
        "power_price": "0.05",
        "currency": "USDT",
    }
    data.update(overrides)
    return data


def test_usdt_request_has_no_local_view(client):
    body = client.post(
        "/api/v1/internal/calc", json=_payload(currency="USDT"), headers=_headers()
    ).json()
    assert body["allowed"] is True
    assert body["local"] is None


def test_local_view_converts_headline_figures(client):
    body = client.post(
        "/api/v1/internal/calc", json=_payload(currency="RUB"), headers=_headers()
    ).json()
    assert body["allowed"] is True
    # USDT result is untouched/authoritative.
    usdt_day = Decimal(body["result"]["net_profit_day"])
    local = body["local"]
    assert local is not None
    assert local["currency"] == "RUB"
    assert local["symbol"] == "₽"
    assert Decimal(local["rate"]) == Decimal("90")
    # The local day figure is the USDT one × 90, rounded to 2 places.
    expected = (usdt_day * Decimal("90")).quantize(Decimal("0.01"))
    assert Decimal(local["net_profit_day"]) == expected


def test_local_view_absent_when_rate_unavailable(client, monkeypatch):
    # FX source down and nothing persisted → graceful fallback to USDT (no local).
    from app.services.fx.provider import FxFetchError

    def _boom(_fiats):
        raise FxFetchError("down")

    monkeypatch.setattr(fx_service, "fetch_anchor_rates", _boom)
    body = client.post(
        "/api/v1/internal/calc", json=_payload(currency="RUB"), headers=_headers()
    ).json()
    assert body["allowed"] is True
    assert body["result"] is not None  # USDT result still served
    assert body["local"] is None
