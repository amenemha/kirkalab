"""Доходность не-SHA-256 алгоритмов (Scrypt, kHeavyHash, Equihash, X11 …).

Источник — публичный JSON по ASIC-монетам (сеть, награда, время блока, курс к
BTC), без ключа. Доход за 1 H/s в сутки по монете:

    coins/day = H / nethash * (86400 / block_time) * block_reward

Для Scrypt учитывается merged-mining: LTC + DOGE суммируются.
Кэш в процессе на 10 минут, при сбое — последний удачный ответ (до 6 часов).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.core.config import get_settings

# Алгоритм каталога → монеты (ключи источника). Пустой список — сеть ушла
# с этого алгоритма, считать нечего.
ALGO_COINS: dict[str, list[str]] = {
    "scrypt": ["Litecoin", "Dogecoin"],
    "kheavyhash": ["Kaspa"],
    "equihash": ["Zcash"],
    "x11": ["Dash"],
    "kadena (blake2s)": ["Kadena"],
    "kadena": ["Kadena"],
    "blake2s": ["Kadena"],
    "eaglesong": ["Nervos"],
    "ethash": ["EthereumClassic"],
    "etchash": ["EthereumClassic"],
    "cuckatoo31": ["MWC-CT31"],
    "cuckatoo32": ["Grin-CT32"],
    "blake3": ["Alephium"],
    "randomx": ["Monero"],
    # Устаревшие: сети сменили алгоритм.
    "blake256r14": [],
    "cryptonight": [],
    "blake2b": [],
}
OBSOLETE_NOTE = {
    "blake256r14": "Decred перешёл на другой алгоритм — эти асики больше не добывают DCR",
    "cryptonight": "Monero перешёл на RandomX — CryptoNight-асики больше не добывают XMR",
    "blake2b": "Siacoin сменил алгоритм — эти асики больше не добывают SC",
}

UNIT_MULT: dict[str, Decimal] = {
    "h/s": Decimal(1), "kh/s": Decimal(10) ** 3, "mh/s": Decimal(10) ** 6, "gh/s": Decimal(10) ** 9,
    "th/s": Decimal(10) ** 12, "ph/s": Decimal(10) ** 15,
    "sol/s": Decimal(1), "ksol/s": Decimal(10) ** 3, "msol/s": Decimal(10) ** 6,
    "gps": Decimal(1), "g/s": Decimal(1),
}


class AlgoUnavailableError(RuntimeError):
    """Нет данных по монетам алгоритма."""


@dataclass(frozen=True)
class AlgoMarket:
    algorithm: str
    btc_price_usdt: Decimal
    usd_per_hs_day: Decimal
    coins_per_hs_day: tuple  # ((tag, coins_per_hs_day, price_usdt), ...)
    captured_at: float


_cache: dict = {"t": 0.0, "data": None}
_lock = threading.Lock()
TTL = 600
STALE = 6 * 3600


def unit_mult(unit: str | None) -> Decimal:
    return UNIT_MULT.get((unit or "TH/s").strip().lower(), Decimal(10) ** 12)


def is_sha256(algorithm: str | None) -> bool:
    return (algorithm or "SHA-256").strip().upper().replace("_", "-") in ("SHA-256", "SHA256", "SHA-256D")


def _fetch() -> dict:
    url = get_settings().whattomine_asic_url
    r = httpx.get(url, timeout=10, headers={"User-Agent": "Kirkalab/1.0"})
    r.raise_for_status()
    return r.json()["coins"]


def _coins() -> tuple[dict, float]:
    now = time.time()
    with _lock:
        if _cache["data"] is not None and now - _cache["t"] < TTL:
            return _cache["data"], _cache["t"]
    try:
        data = _fetch()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        with _lock:
            if _cache["data"] is not None and now - _cache["t"] < STALE:
                return _cache["data"], _cache["t"]
        raise AlgoUnavailableError(f"нет данных по монетам: {exc}") from exc
    with _lock:
        _cache.update(t=now, data=data)
    return data, now


def reset_cache() -> None:
    with _lock:
        _cache.update(t=0.0, data=None)


def algo_market(algorithm: str) -> AlgoMarket:
    key = (algorithm or "").strip().lower()
    if key not in ALGO_COINS:
        raise ValueError(f"алгоритм {algorithm} пока не поддерживается калькулятором")
    names = ALGO_COINS[key]
    if not names:
        raise ValueError(OBSOLETE_NOTE.get(key, f"по алгоритму {algorithm} сейчас нечего добывать"))
    coins, t = _coins()
    btc = coins.get("Bitcoin") or {}
    btc_usd = Decimal(str(btc.get("exchange_rate") or 0))
    if btc_usd <= 0:
        raise AlgoUnavailableError("нет курса BTC")
    total = Decimal(0)
    parts = []
    for n in names:
        c = coins.get(n)
        if not c:
            continue
        nethash = Decimal(str(c.get("nethash") or 0))
        bt = Decimal(str(c.get("block_time") or 0))
        reward = Decimal(str(c.get("block_reward") or 0))
        rate_btc = Decimal(str(c.get("exchange_rate") or 0))
        if nethash <= 0 or bt <= 0:
            continue
        per_hs = (Decimal(86400) / bt) * reward / nethash
        price = rate_btc * btc_usd
        total += per_hs * price
        parts.append((c.get("tag") or n, per_hs, price))
    if not parts:
        raise AlgoUnavailableError(f"нет данных по монетам алгоритма {algorithm}")
    return AlgoMarket(algorithm=algorithm, btc_price_usdt=btc_usd, usd_per_hs_day=total,
                      coins_per_hs_day=tuple(parts), captured_at=t)
