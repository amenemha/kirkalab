"""FX rate service: fetch/persist, fallback, conversion (Redis mocked out)."""
from decimal import Decimal

import pytest

from app.crud import fx as crud_fx
from app.services.fx import service as fx_service
from app.services.fx.provider import FxFetchError


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Force the cache-less path: no live Redis in CI.

    Returning None from ``_redis_client`` makes every cache read a miss and every
    write a no-op, so the service exercises the upstream/DB layers deterministically.
    """
    monkeypatch.setattr(fx_service, "_redis_client", lambda: None)


def _rates():
    return {
        "USD": Decimal("1.0"),
        "RUB": Decimal("90.0"),
        "KZT": Decimal("470.0"),
        "UAH": Decimal("41.0"),
        "EUR": Decimal("0.92"),
    }


def test_refresh_fetches_persists_and_returns(db, monkeypatch):
    monkeypatch.setattr(fx_service, "fetch_anchor_rates", lambda fiats: _rates())

    rates = fx_service.refresh_anchor_rates(db)
    assert rates["RUB"] == Decimal("90.0")

    # A snapshot row was persisted per pair.
    row = crud_fx.get_latest_fx_rate(db, base_currency="USDT", quote_currency="RUB")
    assert row is not None
    assert Decimal(row.rate) == Decimal("90.0")


def test_get_anchor_rate_usdt_is_one(db):
    assert fx_service.get_anchor_rate(db, "USDT") == Decimal(1)


def test_convert_usdt_to_fiat(db, monkeypatch):
    monkeypatch.setattr(fx_service, "fetch_anchor_rates", lambda fiats: _rates())
    out = fx_service.convert(db, Decimal("10"), from_code="USDT", to_code="RUB")
    assert out == Decimal("900.00")


def test_convert_fiat_to_usdt(db, monkeypatch):
    monkeypatch.setattr(fx_service, "fetch_anchor_rates", lambda fiats: _rates())
    # 900 RUB / 90 = 10 USDT
    out = fx_service.convert(db, Decimal("900"), from_code="RUB", to_code="USDT")
    assert out == Decimal("10.00")


def test_convert_fiat_to_fiat_cross_rate(db, monkeypatch):
    monkeypatch.setattr(fx_service, "fetch_anchor_rates", lambda fiats: _rates())
    # 90 RUB = 1 USDT = 0.92 EUR
    out = fx_service.convert(db, Decimal("90"), from_code="RUB", to_code="EUR")
    assert out == Decimal("0.92")


def test_convert_same_currency_is_identity(db, monkeypatch):
    monkeypatch.setattr(fx_service, "fetch_anchor_rates", lambda fiats: _rates())
    out = fx_service.convert(db, Decimal("12.3456"), from_code="RUB", to_code="RUB")
    assert out == Decimal("12.35")


def test_fallback_to_persisted_rate_when_source_down(db, monkeypatch):
    # Seed a persisted rate directly.
    crud_fx.add_fx_rate(
        db, base_currency="USDT", quote_currency="RUB", rate=Decimal("88.0")
    )

    def _boom(_fiats):
        raise FxFetchError("down")

    monkeypatch.setattr(fx_service, "fetch_anchor_rates", _boom)

    rates = fx_service.refresh_anchor_rates(db)
    assert rates["RUB"] == Decimal("88.0")


def test_no_source_and_no_persisted_raises(db, monkeypatch):
    def _boom(_fiats):
        raise FxFetchError("down")

    monkeypatch.setattr(fx_service, "fetch_anchor_rates", _boom)

    with pytest.raises(fx_service.RateUnavailableError):
        fx_service.refresh_anchor_rates(db)


def test_get_anchor_rate_falls_back_to_stale_row(db, monkeypatch):
    # Source down, but a stale persisted row exists for the pair.
    crud_fx.add_fx_rate(
        db, base_currency="USDT", quote_currency="EUR", rate=Decimal("0.91")
    )

    def _boom(_fiats):
        raise FxFetchError("down")

    monkeypatch.setattr(fx_service, "fetch_anchor_rates", _boom)
    # refresh raises (no full set), but a single-pair lookup still resolves.
    rate = fx_service.get_anchor_rate(db, "EUR")
    assert rate == Decimal("0.91")


def test_convert_unknown_currency_raises(db, monkeypatch):
    def _boom(_fiats):
        raise FxFetchError("down")

    monkeypatch.setattr(fx_service, "fetch_anchor_rates", _boom)
    with pytest.raises(fx_service.RateUnavailableError):
        fx_service.convert(db, Decimal("1"), from_code="USDT", to_code="RUB")
