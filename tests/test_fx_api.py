"""Internal FX endpoints: /fx/rates and /fx/convert (secret-gated)."""
from decimal import Decimal

import pytest

from app.services.fx import service as fx_service

BOT_SECRET = "test-bot-secret"


def _headers():
    return {"X-Bot-Secret": BOT_SECRET}


def _rates():
    return {
        "USD": Decimal("1.0"),
        "RUB": Decimal("90.0"),
        "KZT": Decimal("470.0"),
        "UAH": Decimal("41.0"),
        "EUR": Decimal("0.92"),
    }


@pytest.fixture(autouse=True)
def _mock_fx(monkeypatch):
    monkeypatch.setattr(fx_service, "_redis_client", lambda: None)
    monkeypatch.setattr(fx_service, "fetch_anchor_rates", lambda fiats: _rates())


def test_rates_requires_secret(client):
    assert client.get("/api/v1/internal/fx/rates").status_code == 403


def test_rates_returns_catalog_and_rates(client):
    body = client.get("/api/v1/internal/fx/rates", headers=_headers()).json()
    assert body["base"] == "USDT"
    assert body["rates"]["RUB"] == "90.00000000" or Decimal(
        body["rates"]["RUB"]
    ) == Decimal("90")
    codes = {c["code"] for c in body["currencies"]}
    assert {"USDT", "USD", "RUB", "KZT", "UAH", "EUR"} <= codes


def test_convert_requires_secret(client):
    resp = client.post(
        "/api/v1/internal/fx/convert",
        json={"amount": "10", "from_currency": "USDT", "to_currency": "RUB"},
    )
    assert resp.status_code == 403


def test_convert_usdt_to_rub(client):
    resp = client.post(
        "/api/v1/internal/fx/convert",
        headers=_headers(),
        json={"amount": "10", "from_currency": "USDT", "to_currency": "RUB"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["converted"]) == Decimal("900.00")
    assert body["to_currency"] == "RUB"


def test_convert_unknown_currency_4xx(client):
    resp = client.post(
        "/api/v1/internal/fx/convert",
        headers=_headers(),
        json={"amount": "10", "from_currency": "USDT", "to_currency": "ZZZ"},
    )
    assert resp.status_code == 422
