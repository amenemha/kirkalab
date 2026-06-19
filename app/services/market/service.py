"""Market data service: caching, persistence and fallback.

Layered lookup used by the calc core:

1. In-memory TTL cache (process-local, avoids hammering upstream).
2. On cache miss/expiry, fetch from CoinGecko + mempool, persist a snapshot,
   and refresh the cache.
3. If the upstream fetch fails, fall back to the most recent persisted snapshot
   *as long as it is within the staleness window*.
4. If nothing usable exists, raise ``MarketUnavailableError``.

No external call happens on every calc: callers go through
``get_market_data`` which only refreshes when the cache is stale.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.crud import market as crud_market
from app.services.calc.core import MarketData
from app.services.market.provider import (
    MarketFetchError,
    RawMarketData,
    fetch_market_data,
)


class MarketUnavailableError(RuntimeError):
    """No fresh data and no usable fallback snapshot."""


@dataclass
class _CacheEntry:
    data: MarketData
    captured_at: datetime


# Process-local cache. A lock keeps concurrent refreshes from racing.
_cache: dict[str, _CacheEntry] = {}
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def reset_cache() -> None:
    """Clear the in-memory cache (used by tests)."""
    with _lock:
        _cache.clear()


def _to_market_data(raw: RawMarketData) -> MarketData:
    return MarketData(
        btc_price_usdt=raw.price_usdt,
        network_difficulty=raw.network_difficulty,
        block_reward_btc=raw.block_reward_btc,
    )


def refresh_market_data(db: Session, coin_code: str = "BTC") -> tuple[MarketData, datetime]:
    """Force an upstream fetch, persist a snapshot, and update the cache.

    Returns the data and its capture time. Raises ``MarketUnavailableError``
    only if the upstream fetch fails AND there is no usable fallback snapshot.
    """
    try:
        raw = fetch_market_data()
    except MarketFetchError:
        fallback = _fallback_from_db(db, coin_code)
        if fallback is not None:
            return fallback
        raise MarketUnavailableError(
            "external market data is unavailable and no recent snapshot exists"
        )

    snapshot = crud_market.add_snapshot(
        db,
        source="coingecko+mempool",
        network_difficulty=raw.network_difficulty,
        block_reward_btc=raw.block_reward_btc,
        price_usdt=raw.price_usdt,
        coin_code=coin_code,
    )
    data = _to_market_data(raw)
    captured_at = _ensure_aware(snapshot.captured_at)
    with _lock:
        _cache[coin_code] = _CacheEntry(data=data, captured_at=captured_at)
    return data, captured_at


def _fallback_from_db(
    db: Session, coin_code: str
) -> tuple[MarketData, datetime] | None:
    settings = get_settings()
    snapshot = crud_market.get_latest_snapshot(db, coin_code=coin_code)
    if snapshot is None:
        return None
    captured_at = _ensure_aware(snapshot.captured_at)
    max_age = timedelta(seconds=settings.market_max_staleness_seconds)
    if _now() - captured_at > max_age:
        return None
    data = MarketData(
        btc_price_usdt=Decimal(snapshot.price_usdt),
        network_difficulty=Decimal(snapshot.network_difficulty),
        block_reward_btc=Decimal(snapshot.block_reward_btc),
    )
    # Warm the in-memory cache from the durable fallback.
    with _lock:
        _cache[coin_code] = _CacheEntry(data=data, captured_at=captured_at)
    return data, captured_at


def get_market_data(
    db: Session, coin_code: str = "BTC"
) -> tuple[MarketData, datetime]:
    """Return market data for the calc core, refreshing lazily when stale.

    Order: fresh in-memory cache -> upstream refresh -> persisted fallback.
    """
    settings = get_settings()
    ttl = timedelta(seconds=settings.market_cache_ttl_seconds)

    with _lock:
        entry = _cache.get(coin_code)
    if entry is not None and _now() - entry.captured_at <= ttl:
        return entry.data, entry.captured_at

    # Cache miss or stale: try a refresh, which itself falls back to the DB.
    return refresh_market_data(db, coin_code=coin_code)
