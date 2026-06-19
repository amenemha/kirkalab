from datetime import timedelta
from decimal import Decimal

import pytest

from app.crud import market as crud_market
from app.services.market import service as market_service
from app.services.market.provider import MarketFetchError, RawMarketData


@pytest.fixture(autouse=True)
def _clear_cache():
    market_service.reset_cache()
    yield
    market_service.reset_cache()


def _raw():
    return RawMarketData(
        price_usdt=Decimal("60000"),
        network_difficulty=Decimal("80000000000000"),
        block_reward_btc=Decimal("3.125"),
    )


def test_refresh_fetches_persists_and_caches(db, monkeypatch):
    monkeypatch.setattr(market_service, "fetch_market_data", _raw)

    data, captured_at = market_service.refresh_market_data(db)
    assert data.btc_price_usdt == Decimal("60000")

    # A snapshot was persisted.
    snap = crud_market.get_latest_snapshot(db)
    assert snap is not None
    assert Decimal(snap.price_usdt) == Decimal("60000")


def test_get_market_data_uses_cache_without_refetch(db, monkeypatch):
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return _raw()

    monkeypatch.setattr(market_service, "fetch_market_data", _counting)

    market_service.get_market_data(db)
    market_service.get_market_data(db)  # served from cache
    assert calls["n"] == 1


def test_fallback_to_recent_snapshot_when_api_down(db, monkeypatch):
    # Seed a recent snapshot directly.
    crud_market.add_snapshot(
        db,
        source="test",
        network_difficulty=Decimal("80000000000000"),
        block_reward_btc=Decimal("3.125"),
        price_usdt=Decimal("59000"),
    )

    def _boom():
        raise MarketFetchError("down")

    monkeypatch.setattr(market_service, "fetch_market_data", _boom)
    market_service.reset_cache()

    data, _ = market_service.get_market_data(db)
    assert data.btc_price_usdt == Decimal("59000")


def test_fallback_rejects_stale_snapshot(db, monkeypatch):
    snap = crud_market.add_snapshot(
        db,
        source="test",
        network_difficulty=Decimal("80000000000000"),
        block_reward_btc=Decimal("3.125"),
        price_usdt=Decimal("59000"),
    )
    # Force the snapshot to be older than the staleness window.
    from datetime import datetime, timezone

    snap.captured_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.add(snap)
    db.commit()

    def _boom():
        raise MarketFetchError("down")

    monkeypatch.setattr(market_service, "fetch_market_data", _boom)
    market_service.reset_cache()

    with pytest.raises(market_service.MarketUnavailableError):
        market_service.get_market_data(db)


def test_no_snapshot_and_api_down_raises(db, monkeypatch):
    def _boom():
        raise MarketFetchError("down")

    monkeypatch.setattr(market_service, "fetch_market_data", _boom)
    market_service.reset_cache()

    with pytest.raises(market_service.MarketUnavailableError):
        market_service.get_market_data(db)
