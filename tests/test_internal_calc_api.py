"""Integration tests for the internal calc endpoint (calc + funnel meta)."""
from decimal import Decimal

import pytest

from app.db import models
from app.services.market import service as market_service
from app.services.market.provider import RawMarketData

BOT_SECRET = "test-bot-secret"
TG_ID = 555001


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


def test_requires_bot_secret(client):
    resp = client.post("/api/v1/internal/calc", json=_payload())
    assert resp.status_code == 403


def test_calc_returns_result_and_funnel(client):
    resp = client.post("/api/v1/internal/calc", json=_payload(), headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["allowed"] is True
    assert body["result"]["btc_price_usdt"] == "60000"
    f = body["funnel"]
    assert f["is_pro"] is False
    assert f["stage"] == "local_full"
    assert f["calc_index"] == 1
    assert f["intro_left"] == 5


def test_requires_specs(client):
    bad = _payload()
    del bad["hashrate_ths"]
    del bad["power_w"]
    resp = client.post("/api/v1/internal/calc", json=bad, headers=_headers())
    assert resp.status_code == 422


def test_funnel_stages_progress_intro_then_daily(client):
    # 5 intro calcs: 1-3 local_full, 4-5 local_blurred.
    expected = [
        "local_full",
        "local_full",
        "local_full",
        "local_blurred",
        "local_blurred",
    ]
    for i, stage in enumerate(expected, start=1):
        resp = client.post(
            "/api/v1/internal/calc", json=_payload(), headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        f = resp.json()["funnel"]
        assert f["stage"] == stage, (i, f)
        assert f["calc_index"] == i

    # 6th, 7th, 8th: intro spent -> usdt_only, daily quota 3..1.
    for daily_left in (3, 2, 1):
        resp = client.post(
            "/api/v1/internal/calc", json=_payload(), headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is True
        f = body["funnel"]
        assert f["stage"] == "usdt_only"
        assert f["intro_spent"] is True
        assert f["daily_left"] == daily_left

    # 9th: daily quota exhausted -> blocked, no result.
    resp = client.post(
        "/api/v1/internal/calc", json=_payload(), headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["allowed"] is False
    assert body["result"] is None
    assert body["funnel"]["daily_left"] == 0


def test_pro_user_no_funnel_no_limits(client, db):
    # Pre-create a PRO telegram user.
    user = models.User(
        email=f"tg_{TG_ID}@telegram.bot",
        handle=f"tg_{TG_ID}",
        hashed_password="x",
        telegram_user_id=TG_ID,
        is_pro=True,
    )
    db.add(user)
    db.commit()

    for _ in range(10):
        resp = client.post(
            "/api/v1/internal/calc", json=_payload(), headers=_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["allowed"] is True
        f = body["funnel"]
        assert f["is_pro"] is True
        assert f["stage"] == "pro"
        assert f["daily_left"] is None


def test_blocked_calc_does_not_consume_when_invalid(client):
    # An invalid (free-overrange) request should not record a run.
    resp = client.post(
        "/api/v1/internal/calc",
        json=_payload(quantity=6),
        headers=_headers(),
    )
    assert resp.status_code == 422, resp.text
    # Next valid call is still calc 1.
    resp = client.post("/api/v1/internal/calc", json=_payload(), headers=_headers())
    assert resp.json()["funnel"]["calc_index"] == 1


def test_save_power_price_and_status(client):
    resp = client.post(
        "/api/v1/internal/calc",
        json=_payload(save_power_price=True, power_price="0.07"),
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    status = client.get(
        "/api/v1/internal/calc/status",
        params={"telegram_user_id": TG_ID},
        headers=_headers(),
    )
    assert status.status_code == 200, status.text
    body = status.json()
    assert Decimal(body["default_power_price"]) == Decimal("0.0700")
    # One run done -> next is calc 2.
    assert body["funnel"]["calc_index"] == 2


def test_device_model_id_path(client, db):
    model = models.DeviceModel(
        brand="Bitmain",
        model_name="Antminer S19",
        default_hashrate_ths=Decimal("95.00"),
        default_power_w=3250,
    )
    db.add(model)
    db.commit()
    db.refresh(model)

    resp = client.post(
        "/api/v1/internal/calc",
        json={
            "telegram_user_id": TG_ID,
            "device_model_id": model.id,
            "quantity": 2,
            "power_price": "0.05",
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_model_id"] == model.id
    assert body["result"]["input"]["quantity"] == 2


def test_unknown_device_model_404(client):
    resp = client.post(
        "/api/v1/internal/calc",
        json={
            "telegram_user_id": TG_ID,
            "device_model_id": 999999,
            "power_price": "0.05",
        },
        headers=_headers(),
    )
    assert resp.status_code == 404, resp.text
