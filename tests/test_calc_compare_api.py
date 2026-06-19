from decimal import Decimal
from uuid import uuid4

import pytest

from app.db import models
from app.services.market import service as market_service
from app.services.market.provider import RawMarketData

PASSWORD = "123456Test789"


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
        "hashrate_ths": "140",
        "power_w": 3010,
        "custom_hashrate_ths": "158",
        "custom_power_w": 3620,
        "quantity": 1,
        "power_price": "0.05",
    }
    data.update(overrides)
    return data


def _make_pro_token(client, db):
    email = f"pro_{uuid4().hex[:8]}@example.com"
    handle = f"pro_{uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/users/",
        json={"email": email, "handle": handle, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    user = db.query(models.User).filter_by(email=email).one()
    user.is_pro = True
    db.commit()
    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["access_token"]


def test_compare_anonymous_is_gated(client):
    resp = client.post("/api/v1/calc/compare", json=_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Stock side is fully present.
    assert body["base"]["net_profit_day"]
    # Custom side and delta are withheld behind the PRO gate.
    assert body["custom"] is None
    assert body["delta"]["pro_required"] is True
    assert body["delta"]["delta_profit_day"] is None
    assert body["delta"]["economy_note"] is None


def test_compare_pro_gets_full_delta(client, db):
    token = _make_pro_token(client, db)
    resp = client.post(
        "/api/v1/calc/compare",
        json=_payload(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["custom"] is not None
    delta = body["delta"]
    assert delta["pro_required"] is False
    # Overclock: +18 TH/s, +610 W.
    assert Decimal(delta["delta_hashrate"]) == Decimal("18")
    assert Decimal(delta["delta_power_w"]) == Decimal("610")
    assert delta["economy_note"]


def test_compare_undervolt_pro_shows_power_savings(client, db):
    token = _make_pro_token(client, db)
    resp = client.post(
        "/api/v1/calc/compare",
        json=_payload(custom_hashrate_ths="134", custom_power_w=2520),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    delta = resp.json()["delta"]
    assert Decimal(delta["delta_power_w"]) == Decimal("-490")
    assert Decimal(delta["delta_power_cost_day"]) < 0


def test_compare_requires_custom_source(client):
    resp = client.post(
        "/api/v1/calc/compare",
        json={
            "hashrate_ths": "140",
            "power_w": 3010,
            "power_price": "0.05",
        },
    )
    assert resp.status_code == 422, resp.text
    assert "custom side required" in resp.text


def test_compare_resolves_preset_id(client, db):
    # Seed catalog + presets so a firmware_preset_id resolves.
    from app.db.seed_catalog import seed_catalog
    from app.db.seed_firmware import seed_firmware_presets

    seed_catalog(db)
    seed_firmware_presets(db)
    preset = (
        db.query(models.FirmwarePreset)
        .filter_by(firmware="vnish", preset_name="Turbo")
        .first()
    )
    token = _make_pro_token(client, db)
    resp = client.post(
        "/api/v1/calc/compare",
        json={
            "hashrate_ths": "140",
            "power_w": 3010,
            "firmware_preset_id": preset.id,
            "power_price": "0.05",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    delta = resp.json()["delta"]
    # Custom hashrate comes from the preset.
    assert Decimal(delta["delta_hashrate"]) == preset.hashrate - Decimal("140")
