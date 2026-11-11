"""Рыночный курс USDT/RUB и цены монет с открытой биржевой ленты (без ключей).

Курс ЦБ и «средний по миру» заметно отличаются от того, по которому реально меняют USDT на рубли,
поэтому рубли в Kirka считаются по рыночной середине между покупкой и продажей. Источник людям не показываем.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("kirka.fx")
URL = "https://api.rapira.net/open/market/rates"
TTL = 120
_c: dict = {"at": 0.0, "rows": {}}


def _rows(timeout: float = 6) -> dict[str, dict]:
    if time.time() - _c["at"] < TTL and _c["rows"]:
        return _c["rows"]
    try:
        j = httpx.get(URL, timeout=timeout, headers={"User-Agent": "Kirka"}).json()
        rows = {str(x.get("symbol")): x for x in j.get("data") or [] if x.get("symbol")}
        if rows:
            _c.update(at=time.time(), rows=rows)
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.info("market rates unavailable: %s", type(exc).__name__)
        _c["at"] = time.time() - TTL + 30  # не долбим источник: повтор через 30 с
    return _c["rows"]


def _mid(x: dict) -> float | None:
    try:
        b, a = float(x.get("bidPrice") or 0), float(x.get("askPrice") or 0)
        if b > 0 and a > 0 and a / b < 1.05:
            return (a + b) / 2
        c = float(x.get("close") or 0)
        return c or None
    except (TypeError, ValueError):
        return None


def usdt_rub() -> float | None:
    x = _rows().get("USDT/RUB")
    v = _mid(x) if x else None
    return v if v and 30 < v < 300 else None


def coin_usd() -> dict[str, float]:
    """{'BTC': 73700.0, ...} — цены монет в USDT с той же ленты (запасной вариант)."""
    out = {}
    for sym, x in _rows().items():
        if sym.endswith("/USDT"):
            v = _mid(x)
            if v:
                out[sym.split("/")[0]] = v
    return out
