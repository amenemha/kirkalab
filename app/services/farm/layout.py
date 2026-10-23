"""Container layout: sections (subnets / cabinets) → racks → shelves → places.

CROFT gives no rack/shelf data, but on Диагональ the IP encodes the place:
10.<site>.<subnet>.<place>; container names carry their subnets ("4к 6-7 …",
"11к 21 Ф24 …", "11К HD5 40-41 …"). We infer a template from that and let the
user correct it (stored in FarmContainer.layout).
"""
from __future__ import annotations

import math
import re
from collections import Counter

IP_RE = re.compile(r"^\d{1,3}\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
WK_RE = re.compile(r"(\d{1,3})[xX](\d{1,3})[xX](\d{1,3})$")
RANGE_RE = re.compile(r"(?<![\d.])(\d{1,3})-(\d{1,3})(?![\d.])")
SINGLE_RE = re.compile(r"^\s*\d+\s*[кКаАбБ]\s+(\d{1,3})\b")


def kind_of(name: str, models: list[str]) -> str:
    n = (name or "").lower()
    if "модуль" in n or "ванн" in n:
        return "bath"
    hyd = sum(1 for m in models if m and "hyd" in m.lower())
    if "hydro" in n or "hd5" in n or "гидро" in n or (models and hyd > len(models) / 2):
        return "hydro"
    return "air"


def subnets_from_name(name: str) -> list[int]:
    m = RANGE_RE.search(name or "")
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= a <= b <= 255 and b - a <= 3:
            return list(range(a, b + 1))
    m = SINGLE_RE.search(name or "")
    if m:
        return [int(m.group(1))]
    return []


def addr(ip: str | None, worker: str | None) -> tuple[int, int] | None:
    """(subnet, place) from IP; worker suffix as fallback."""
    m = IP_RE.match((ip or "").strip())
    if m:
        return int(m.group(2)), int(m.group(3))
    m = WK_RE.search((worker or "").strip())
    if m:
        return int(m.group(2)), int(m.group(3))
    return None


def _air_shape(n: int) -> tuple[int, int, int]:
    """racks, shelves, places for one air subnet of n places (BitCube/Гексакон family)."""
    if n <= 100:
        return 5, 5, 4
    if n <= 120:
        return 5, 6, 4
    if n <= 150:
        return 5, 6, 5
    per = math.ceil(n / 5)
    return 5, 6, math.ceil(per / 6)


def infer(name: str, devices: list[dict]) -> dict:
    """devices: [{id, ip, worker, model}] → layout spec (without placement)."""
    models = [d.get("model") or "" for d in devices]
    kind = kind_of(name, models)
    seen: Counter = Counter()
    top: dict[int, int] = {}
    for d in devices:
        a = addr(d.get("ip"), None)
        if a:
            seen[a[0]] += 1
            top[a[0]] = max(top.get(a[0], 0), a[1])
    nets = subnets_from_name(name)
    if not nets:
        nets = sorted(s for s, c in seen.items() if c >= 3)
    secs: list[dict] = []
    if kind == "bath":
        n = max(len(devices), 1)
        cols = min(20, max(5, math.ceil(math.sqrt(n * 2))))
        secs.append(dict(title="Ванны", subnet=None, first=1, racks=1, shelves=math.ceil(n / cols), places=cols, by="order"))
    elif kind == "hydro" and len(nets) == 1 and top.get(nets[0], 0) <= 200:
        # Antspace HK3 193 (hydro Bitmain): cabinet A 1–93 left, B 94–193 right, 7 shelves
        s = nets[0]
        secs.append(dict(title="Кабинет А", subnet=s, first=1, last=93, racks=1, shelves=7, places=14, by="ip"))
        secs.append(dict(title="Кабинет Б", subnet=s, first=94, last=193, racks=1, shelves=7, places=15, by="ip"))
    elif kind == "hydro":
        for i, s in enumerate(nets):
            n = max(top.get(s, 0), 1)
            secs.append(dict(title=f"Секция {'АБВГ'[i] if i < 4 else i + 1} · подсеть {s}", subnet=s, first=1, racks=1, shelves=7, places=math.ceil(n / 7), by="ip"))
    else:
        n = max([top.get(x, 0) for x in nets] + [100])
        r, sh, p = _air_shape(n)
        for i, s in enumerate(nets):
            secs.append(dict(title=f"Подсеть {s}", subnet=s, first=1, racks=r, shelves=sh, places=p, rack_from=i * r + 1, by="ip"))
    tpl = {"air": "Воздух", "hydro": "Гидро", "bath": "Ванны"}[kind]
    return {"kind": kind, "template": tpl, "auto": True, "sections": secs, "start": "top-left"}


def place(layout: dict, devices: list[dict]) -> dict:
    """→ {placed: {id: [sec, rack, shelf, slot, number]}, unplaced: [id], doubles: [id]}."""
    secs = layout.get("sections") or []
    placed: dict[int, list] = {}
    taken: dict[tuple, int] = {}
    unplaced, doubles = [], []
    order_i = 0
    start_bottom = layout.get("start") == "bottom-left"
    rank = {"working": 0, "suspended": 1, "offline": 2}
    for d in sorted(devices, key=lambda x: (rank.get(x.get("status"), 3), x.get("ip") or "~", x["id"])):
        a = addr(d.get("ip"), d.get("worker"))
        hit = None
        for si, s in enumerate(secs):
            per_rack = s["shelves"] * s["places"]
            cap = per_rack * s["racks"]
            if s.get("by") == "order":
                if order_i < cap:
                    num = order_i + 1
                    order_i += 1
                    hit = (si, num, s)
                break
            if not a or a[0] != s.get("subnet"):
                continue
            num = a[1]
            first, last = s.get("first", 1), s.get("last") or (s.get("first", 1) + cap - 1)
            if first <= num <= last and num - first < cap:
                hit = (si, num, s)
                break
        if not hit:
            unplaced.append(d["id"])
            continue
        si, num, s = hit
        i = num - s.get("first", 1)
        per_rack = s["shelves"] * s["places"]
        rack, j = divmod(i, per_rack)
        shelf, slot = divmod(j, s["places"])
        if start_bottom:
            shelf = s["shelves"] - 1 - shelf
        key = (si, rack, shelf, slot)
        if key in taken:
            doubles.append(d["id"])
            continue
        taken[key] = d["id"]
        placed[d["id"]] = [si, rack, shelf, slot, num]
    return {"placed": placed, "unplaced": unplaced, "doubles": doubles}


def clean(spec: dict) -> dict:
    """Validate a user-edited layout."""
    out = {"kind": spec.get("kind") if spec.get("kind") in ("air", "hydro", "bath") else "air",
           "template": str(spec.get("template") or "")[:60], "auto": False,
           "start": "bottom-left" if spec.get("start") == "bottom-left" else "top-left", "sections": []}
    for s in (spec.get("sections") or [])[:12]:
        def n(k, lo, hi, dflt):
            try:
                return max(lo, min(hi, int(s.get(k, dflt))))
            except (TypeError, ValueError):
                return dflt
        sec = dict(title=str(s.get("title") or "")[:40], subnet=n("subnet", 0, 255, 0) if s.get("subnet") is not None else None,
                   first=n("first", 0, 999, 1), racks=n("racks", 1, 30, 1), shelves=n("shelves", 1, 20, 6), places=n("places", 1, 40, 5),
                   by="order" if s.get("by") == "order" else "ip")
        if s.get("last"):
            sec["last"] = n("last", sec["first"], 999, sec["first"])
        if s.get("rack_from"):
            sec["rack_from"] = n("rack_from", 1, 99, 1)
        out["sections"].append(sec)
    return out
