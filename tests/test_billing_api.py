"""Integration tests for the billing internal endpoints (plans + activate)."""
from decimal import Decimal

import pytest

from app.db import models
from app.services.market import service as market_service
from app.services.market.provider import RawMarketData

BOT_SECRET = "test-bot-secret"
TG_ID = 660001


def _headers():
    return {"X-Bot-Secret": BOT_SECRET}


@pytest.fixture(autouse=True)
def _seed_market(monkeypatch):
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
    yield
    market_service.reset_cache()


def test_plans_requires_secret(client):
    assert client.get("/api/v1/internal/plans").status_code == 403


def test_plans_lists_active_with_prices(client):
    body = client.get("/api/v1/internal/plans", headers=_headers()).json()
    codes = {p["code"] for p in body["plans"]}
    assert {"free", "pro_monthly", "pro_yearly"} <= codes
    pro = next(p for p in body["plans"] if p["code"] == "pro_monthly")
    assert pro["price_stars"] == 250
    assert pro["currency"] == "XTR"
    assert pro["period_days"] == 30
    yearly = next(p for p in body["plans"] if p["code"] == "pro_yearly")
    assert yearly["price_stars"] == 2500
    assert yearly["period_days"] == 365


def test_activate_requires_secret(client):
    resp = client.post(
        "/api/v1/internal/billing/activate",
        json={
            "telegram_id": TG_ID,
            "plan_code": "pro_monthly",
            "telegram_payment_charge_id": "c1",
            "total_amount": 250,
        },
    )
    assert resp.status_code == 403


def test_activate_grants_pro(client):
    resp = client.post(
        "/api/v1/internal/billing/activate",
        json={
            "telegram_id": TG_ID,
            "plan_code": "pro_monthly",
            "telegram_payment_charge_id": "charge-grant",
            "total_amount": 250,
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_pro"] is True
    assert body["plan_code"] == "pro_monthly"
    assert body["status"] == "active"
    assert body["already_applied"] is False
    assert body["expires_at"] is not None

    # The profile now reports PRO.
    prof = client.get(
        "/api/v1/internal/profile",
        params={"telegram_user_id": TG_ID},
        headers=_headers(),
    ).json()
    assert prof["is_pro"] is True


def test_activate_idempotent_repeat(client):
    payload = {
        "telegram_id": 660002,
        "plan_code": "pro_monthly",
        "telegram_payment_charge_id": "charge-rep",
        "total_amount": 250,
    }
    first = client.post(
        "/api/v1/internal/billing/activate", json=payload, headers=_headers()
    ).json()
    second = client.post(
        "/api/v1/internal/billing/activate", json=payload, headers=_headers()
    ).json()
    assert first["expires_at"] == second["expires_at"]
    assert second["already_applied"] is True


def test_activate_unknown_plan_400(client):
    resp = client.post(
        "/api/v1/internal/billing/activate",
        json={
            "telegram_id": 660003,
            "plan_code": "ghost",
            "telegram_payment_charge_id": "charge-ghost",
            "total_amount": 10,
        },
        headers=_headers(),
    )
    assert resp.status_code == 400, resp.text


def test_pro_user_calc_unlimited_after_activation(client):
    tg = 660010
    client.post(
        "/api/v1/internal/billing/activate",
        json={
            "telegram_id": tg,
            "plan_code": "pro_yearly",
            "telegram_payment_charge_id": "charge-unl",
            "total_amount": 2500,
        },
        headers=_headers(),
    )
    # A PRO user has no funnel/limits: many calcs all return pro stage.
    for _ in range(8):
        resp = client.post(
            "/api/v1/internal/calc",
            json={
                "telegram_user_id": tg,
                "hashrate_ths": "100",
                "power_w": 3250,
                "quantity": 1,
                "power_price": "0.05",
                "currency": "USDT",
            },
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is True
        assert body["funnel"]["stage"] == "pro"
