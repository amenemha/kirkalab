"""ViaBTC observer (read-only link) client: /res/observer/*."""
from __future__ import annotations

import re
import time

import httpx

API = "https://www.viabtc.com/res/observer"
MULT = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "Z": 1e21}
_KEY = re.compile(r"access_key=([0-9a-fA-F]{16,64})")


def parse_key(link: str) -> str | None:
    link = (link or "").strip()
    m = _KEY.search(link)
    if m:
        return m.group(1).lower()
    if re.fullmatch(r"[0-9a-fA-F]{16,64}", link):
        return link.lower()
    return None


def coin_from_link(link: str) -> str | None:
    m = re.search(r"[?&]coin=([A-Za-z0-9]+)", link or "")
    return m.group(1).upper() if m else None


def unit_mult(u: str | None) -> float:
    u = (u or "").strip().upper().replace("H/S", "")
    return MULT.get(u[:1] if u else "", 1.0)


def hs(v) -> float | None:
    """'11.722P' → 1.1722e16; '600479950316066' → float; None/'' → None."""
    if v is None or v == "":
        return None
    s = str(v).strip().upper().replace("H/S", "")
    m = re.fullmatch(r"([0-9.]+)\s*([KMGTPEZ]?)", s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return None
    return float(m.group(1)) * MULT[m.group(2)]


def num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


class ViaBTCError(RuntimeError):
    pass


class ViaBTC:
    def __init__(self, timeout: float = 20.0, pause: float = 0.25):
        self.http = httpx.Client(timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/json",
        })
        self.pause = pause

    def get(self, path: str, key: str, **params):
        params = {k: v for k, v in params.items() if v not in (None, 0, "")}
        params["access_key"] = key
        r = self.http.get(f"{API}/{path}", params=params)
        time.sleep(self.pause)
        j = r.json()
        if j.get("code") != 0:
            raise ViaBTCError(f"{j.get('code')} {j.get('message')}")
        return j.get("data") or {}

    def info(self, key):
        return self.get("info", key)

    def home(self, key, coin, user_id=0):
        return self.get("home", key, coin=coin, user_id=user_id)

    def hourly(self, key, coin, user_id=0) -> dict[int, tuple[float, int | None, float | None]]:
        d = self.get("hashrate/chart", key, coin=coin, interval="hour", utc="true", user_id=user_id)
        m = unit_mult(d.get("unit"))
        start = int(d.get("start") or 0) // 1000
        hr = d.get("hashrate") or []
        aw = d.get("active_workers") or []
        rj = d.get("reject_rate") or []
        out = {}
        for i, v in enumerate(hr):
            if v is None:
                continue
            out[start + i * 3600] = (float(v) * m, (aw[i] if i < len(aw) else None), num(rj[i]) if i < len(rj) else None)
        return out

    def daily(self, key, coin, user_id=0) -> dict[int, float]:
        """90 days of daily average hashrate (UTC days), H/s."""
        d = self.get("hashrate/chart", key, coin=coin, interval="day", utc="true", user_id=user_id)
        m = unit_mult(d.get("unit"))
        start = int(d.get("start") or 0) // 1000
        return {start + i * 86400: float(v) * m for i, v in enumerate(d.get("hashrate") or []) if v is not None}

    def profit_days(self, key, coin, user_id=0, pages: int = 2, limit: int = 50) -> list[dict]:
        rows = []
        for p in range(1, pages + 1):
            d = self.get("profit/detail", key, coin=coin, user_id=user_id, page=p, limit=limit)
            rows += d.get("data") or []
            if not d.get("has_next"):
                break
        return rows

    def workers(self, key, coin, user_id=0, max_pages: int = 20) -> list[dict]:
        rows = []
        for p in range(1, max_pages + 1):
            d = self.get("worker", key, coin=coin, user_id=user_id, page=p, limit=100)
            rows += d.get("data") or []
            if not d.get("has_next"):
                break
        return rows
