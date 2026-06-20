"""FX rate service: fetch, cache (Redis), persist and convert.

Layered lookup for ``1 USDT = ? <fiat>`` (the anchor rate every conversion is
built from):

1. Redis cache (TTL, process-shared, avoids hammering CoinGecko). Lazily
   imported and best-effort: a missing/broken Redis never fails a conversion.
2. On cache miss, fetch all configured fiats from CoinGecko in one call, persist
   a snapshot row per pair into ``fx_rates``, and warm the cache.
3. If the upstream fetch fails, fall back to the most recent persisted
   ``fx_rates`` row per pair (graceful degradation — the last good rate).
4. If a target currency has neither a fresh nor a persisted rate, the conversion
   helper raises ``RateUnavailableError`` so the caller can fall back to USDT.

The anchor (``USDT``) converts to itself at rate 1, so a request for USDT never
needs the network. Conversions between two fiats go through the anchor cross
rate (see :mod:`app.services.fx.convert`).
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.crud import fx as crud_fx
from app.services.fx.convert import convert_amount, cross_rate
from app.services.fx.provider import ANCHOR, FxFetchError, fetch_anchor_rates

logger = logging.getLogger("app.fx")

# Redis key holding the JSON map ``{fiat_code: rate_str}`` of anchor rates.
_CACHE_KEY = "fx:anchor_rates"


class RateUnavailableError(RuntimeError):
    """No fresh and no persisted rate exists for a requested currency."""


def _redis_client():
    """Best-effort Redis client, or None if unavailable.

    Lazily imported so the backend (and the no-Redis CI) does not require the
    ``redis`` package to import this module. Any connection problem returns
    None — Redis is a cache, never a hard dependency for a conversion.
    """
    settings = get_settings()
    try:
        import redis  # noqa: PLC0415 — lazy so redis stays an optional cache dep
    except ImportError:
        return None
    try:
        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        return client
    except Exception as exc:  # noqa: BLE001 — cache must never break a conversion
        # Log only the exception class: the Redis URL can carry credentials.
        logger.warning("FX cache unavailable: %s", type(exc).__name__)
        return None


def _cache_get() -> dict[str, Decimal] | None:
    client = _redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_CACHE_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX cache read failed: %s", type(exc).__name__)
        return None
    finally:
        _close(client)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return {k: Decimal(str(v)) for k, v in data.items()}
    except (ValueError, TypeError):
        return None


def _cache_set(rates: dict[str, Decimal]) -> None:
    client = _redis_client()
    if client is None:
        return
    settings = get_settings()
    payload = json.dumps({k: str(v) for k, v in rates.items()})
    try:
        client.set(_CACHE_KEY, payload, ex=settings.fx_cache_ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX cache write failed: %s", type(exc).__name__)
    finally:
        _close(client)


def _close(client) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass


def _fiat_codes(db: Session) -> list[str]:
    """Active fiat codes (anchor excluded — it is rate 1 to itself)."""
    return [
        c.code
        for c in crud_fx.list_currencies(db, active_only=True)
        if c.is_fiat and c.code != ANCHOR
    ]


def refresh_anchor_rates(db: Session) -> dict[str, Decimal]:
    """Fetch all fiat anchor rates from upstream, persist + cache them.

    Returns the ``{fiat_code: rate}`` map (``1 USDT = rate fiat``). On upstream
    failure, falls back to the latest persisted rate per pair so a refresh still
    yields whatever is durably known; raises only if nothing at all exists.
    """
    fiats = _fiat_codes(db)
    if not fiats:
        return {}
    try:
        rates = fetch_anchor_rates(fiats)
        for code, rate in rates.items():
            # Append-only history, but skip writing a row identical to the latest
            # one: it keeps the history meaningful (one row per actual change) and
            # avoids the (base, quote, fetched_at) unique collision when several
            # refreshes land within the same clock second.
            latest = crud_fx.get_latest_fx_rate(
                db, base_currency=ANCHOR, quote_currency=code
            )
            if latest is not None and Decimal(latest.rate) == rate:
                continue
            crud_fx.add_fx_rate(
                db,
                base_currency=ANCHOR,
                quote_currency=code,
                rate=rate,
                source="coingecko",
            )
        _cache_set(rates)
        return rates
    except FxFetchError:
        fallback = _fallback_rates(db, fiats)
        if fallback:
            logger.warning("FX upstream down; using persisted fallback rates")
            return fallback
        raise RateUnavailableError(
            "FX source unavailable and no persisted rates exist"
        )


def _fallback_rates(db: Session, fiats: list[str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for code in fiats:
        row = crud_fx.get_latest_fx_rate(
            db, base_currency=ANCHOR, quote_currency=code
        )
        if row is not None:
            out[code] = Decimal(row.rate)
    return out


def get_anchor_rates(db: Session) -> dict[str, Decimal]:
    """Return ``{fiat_code: rate}`` (1 USDT = rate fiat), refreshing lazily.

    Order: Redis cache -> upstream refresh (persist + cache) -> persisted
    fallback. The anchor itself (USDT=1) is implicit and not included here.
    """
    cached = _cache_get()
    if cached:
        return cached
    return refresh_anchor_rates(db)


def get_anchor_rate(db: Session, code: str) -> Decimal:
    """Rate ``1 USDT = ? <code>``. ``USDT`` is 1. Raises if unknown."""
    code = code.upper()
    if code == ANCHOR:
        return Decimal(1)
    rates = get_anchor_rates(db)
    rate = rates.get(code)
    if rate is None:
        # Last resort: a stale persisted row for just this pair.
        row = crud_fx.get_latest_fx_rate(
            db, base_currency=ANCHOR, quote_currency=code
        )
        if row is not None:
            return Decimal(row.rate)
        raise RateUnavailableError(f"no FX rate available for {code}")
    return rate


def convert(
    db: Session,
    amount: Decimal,
    *,
    from_code: str,
    to_code: str,
) -> Decimal:
    """Convert ``amount`` from ``from_code`` to ``to_code`` at the current rate.

    Both sides are resolved against the USDT anchor, so any pair works (including
    fiat↔fiat via the cross rate). The result is rounded to the target currency's
    configured ``decimals``. Raises ``RateUnavailableError`` if a needed rate is
    missing so the caller can degrade to showing USDT."""
    from_code = from_code.upper()
    to_code = to_code.upper()
    target = crud_fx.get_currency(db, to_code)
    decimals = target.decimals if target is not None else 2

    if from_code == to_code:
        return convert_amount(amount, Decimal(1), decimals=decimals)

    if from_code == ANCHOR:
        rate = get_anchor_rate(db, to_code)
    elif to_code == ANCHOR:
        # 1 from = (1 / anchor_rate(from)) USDT
        rate = Decimal(1) / get_anchor_rate(db, from_code)
    else:
        rate = cross_rate(
            base_to_anchor=get_anchor_rate(db, from_code),
            quote_to_anchor=get_anchor_rate(db, to_code),
        )
    return convert_amount(amount, rate, decimals=decimals)
