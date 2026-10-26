"""Общие настройки фермы. Хранятся в farm_settings, значения по умолчанию — здесь."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import farm_models as fm

DEFAULTS: dict = {
    "lowhash_pct": 90,  # Low hashrate: средний хешрейт за час ниже N% от заводского
}
LIMITS = {"lowhash_pct": (30, 99)}


def get_settings(db: Session) -> dict:
    out = dict(DEFAULTS)
    for s in db.scalars(select(fm.FarmSetting)):
        if s.key in DEFAULTS:
            out[s.key] = s.value
    return out


def set_settings(db: Session, values: dict) -> dict:
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValueError(f"неизвестная настройка: {k}")
        lo, hi = LIMITS.get(k, (None, None))
        if lo is not None:
            v = int(v)
            if not lo <= v <= hi:
                raise ValueError(f"{k}: допустимо {lo}–{hi}")
        row = db.get(fm.FarmSetting, k)
        if row is None:
            db.add(fm.FarmSetting(key=k, value=v))
        else:
            row.value = v
    db.commit()
    return get_settings(db)


def low_hash_alarm(prev: dict | None, avg_ths: float | None, nominal: float | None, pct: float, now_iso: str) -> dict | None:
    """Синтетическая ошибка Low hashrate. Включается ниже порога, гаснет только выше порога +2%
    (чтобы не мигала на границе)."""
    if not nominal or avg_ths is None or nominal <= 0:
        return None
    share = avg_ths / nominal * 100
    limit = pct + (2 if prev else 0)
    if share >= limit:
        return None
    return {"code": "LOWHASH", "msg": f"Low hashrate: {share:.0f}% от заводского (порог {pct:.0f}%)",
            "level": 1, "cls": "hashrate", "since": (prev or {}).get("since") or now_iso}
