"""Справочник разъёмов питания по моделям асиков (подстрока модели → разъём).

inlet — разъём на самом асике (блоке питания), cord — кабель/вилка, которой он
включается в PDU. Подбор по самой длинной подходящей подстроке.
verified=False — по документации производителя, сверить по фото шильдика.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

B1 = "220–277 В · 1 фаза"
B3 = "380–415 В · 3 фазы"

PLUGS = [
    # Antminer — гидро (APW11, трёхфазный водозащищённый разъём, кабель не входит)
    dict(pattern="Hyd", inlet="3ф авиа (APW11)", cord="LP-20 3ф", supply=B3, amps=12, psu="APW11",
         note="Кабель в комплект не входит; тип разъёма сверить по фото"),
    dict(pattern="Hydro", inlet="3ф авиа (APW11)", cord="LP-20 3ф", supply=B3, amps=12, psu="APW11",
         note="Кабель в комплект не входит; тип разъёма сверить по фото"),
    # Antminer — APW17 / P-серия
    dict(pattern="T21", inlet="P34", cord="P33–P33", supply=B1, psu="APW17", verified=True,
         note="Под ANTPDU PW12V1"),
    dict(pattern="S21", inlet="P14", cord="C20–P13", supply=B1, psu="APW17", verified=True,
         note="Или P33–P13 к ANTPDU"),
    dict(pattern="S19j XP", inlet="P14", cord="C20–P13", supply=B1, psu="APW17", verified=True),
    dict(pattern="KS5", inlet="P14", cord="C20–P13", supply=B1, psu="APW17"),
    dict(pattern="L9", inlet="P14", cord="C20–P13", supply=B1, psu="APW17", note="Сверить по фото: P13/P33"),
    # Antminer — APW12 / C14
    dict(pattern="S19", inlet="C14", cord="C13", supply=B1, psu="APW12", verified=True,
         note="Часть ревизий APW12 — C20 (кабель C19)"),
    dict(pattern="L7", inlet="C14", cord="C13", supply=B1, psu="APW12", note="Сверить по фото: C14/C20"),
    dict(pattern="Z15", inlet="C14", cord="C13", supply=B1, psu="APW12", note="Сверить по фото"),
    dict(pattern="KS3", inlet="C14", cord="C13", supply=B1, psu="APW12", note="Сверить по фото"),
    # Whatsminer — C20 на блоке, кабель C19
    dict(pattern="Whatsminer", inlet="C20", cord="C19", supply=B1, amps=16, verified=True),
    dict(pattern="M20", inlet="C20", cord="C19", supply=B1, amps=16, verified=True),
    dict(pattern="M21", inlet="C20", cord="C19", supply=B1, amps=16, verified=True),
    dict(pattern="M30", inlet="C20", cord="C19", supply=B1, amps=16, verified=True),
    dict(pattern="M31", inlet="C20", cord="C19", supply=B1, amps=16, verified=True),
    dict(pattern="M50", inlet="C20", cord="C19", supply=B1, amps=16, verified=True),
    dict(pattern="M60", inlet="C20", cord="C19", supply=B1, amps=16),
    # Elphapex — два ввода C20
    dict(pattern="DG1", inlet="2× C20", cord="2× C19", inlets=2, supply=B1, verified=True,
         note="Или разветвитель C20 → 2× C19"),
    dict(pattern="DG-Home", inlet="?", cord="?", supply="220 В", note="Сверить по фото шильдика"),
    # IceRiver / Bombax
    dict(pattern="KS5L", inlet="C20", cord="C19", supply=B1, verified=True),
    dict(pattern="EZ100", inlet="?", cord="?", supply=B1, note="Сверить по фото шильдика"),
]


def seed_plugs(bind) -> int:
    from app.db.energy_models import AsicPlug
    n = 0
    with Session(bind=bind) as s:
        for spec in PLUGS:
            row = s.scalar(select(AsicPlug).where(AsicPlug.pattern == spec["pattern"]))
            if row is None:
                s.add(AsicPlug(**spec))
                n += 1
        s.commit()
    return n


def plug_for(model: str | None, plugs) -> dict | None:
    """plugs — список AsicPlug или dict; самая длинная подстрока, без учёта регистра."""
    if not model:
        return None
    m = model.lower()
    best = None
    for p in plugs:
        pat = (p["pattern"] if isinstance(p, dict) else p.pattern).lower()
        if pat in m and (best is None or len(pat) > len(best[0])):
            best = (pat, p)
    if not best:
        return None
    p = best[1]
    if isinstance(p, dict):
        return dict(p)
    return {k: getattr(p, k) for k in ("pattern", "inlet", "cord", "inlets", "supply", "amps", "psu", "verified", "note")}
