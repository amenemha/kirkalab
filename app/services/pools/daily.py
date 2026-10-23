"""Daily hashrate per local day (MSK by default) from Kirka's hourly archive."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import pool_models as pm


def day_table(db: Session, account_id: int, d_from: date, d_to: date, tz_hours: int = 3) -> list[dict]:
    t0 = int(datetime(d_from.year, d_from.month, d_from.day, tzinfo=timezone.utc).timestamp()) - tz_hours * 3600
    t1 = int(datetime(d_to.year, d_to.month, d_to.day, tzinfo=timezone.utc).timestamp()) - tz_hours * 3600 + 86400
    hourly = {r.ts: r.hs for r in db.scalars(select(pm.PoolHourly).where(
        pm.PoolHourly.account_id == account_id, pm.PoolHourly.ts >= t0, pm.PoolHourly.ts < t1))}
    pool_days = {r.day: r for r in db.scalars(select(pm.PoolDaily).where(
        pm.PoolDaily.account_id == account_id, pm.PoolDaily.day >= d_from - timedelta(days=1), pm.PoolDaily.day <= d_to + timedelta(days=1)))}
    now = datetime.now(timezone.utc).timestamp()
    rows = []
    d = d_from
    while d <= d_to:
        start = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - tz_hours * 3600
        hours = [start + i * 3600 for i in range(24)]
        vals = [hourly[t] for t in hours if t in hourly]
        expected = sum(1 for t in hours if t + 3600 <= now)
        pdx = pool_days.get(d)
        row = {"day": d.isoformat(), "avg_hs": None, "sum_hs": None, "hours": len(vals), "expected": expected,
               "source": "", "profit": pdx.profit if pdx else None, "money_usd": pdx.money_usd if pdx else None,
               "pool_hs": pdx.hs if pdx else None}
        if expected == 0:
            row["source"] = "сутки ещё не начались"
        elif len(vals) == 24:
            s = sum(vals)
            row.update(avg_hs=s / 24, sum_hs=s, source="24 часа")
        elif expected < 24 and len(vals) == expected:
            s = sum(vals)
            row.update(avg_hs=s / len(vals), sum_hs=s, source=f"идут сутки: {len(vals)} из 24 ч")
        elif pdx and pdx.hs:  # archive is incomplete — the pool's own day is a better whole-day figure
            row.update(avg_hs=pdx.hs, sum_hs=pdx.hs * 24, source="суточное значение пула")
        elif vals:
            s = sum(vals)
            row.update(avg_hs=s / len(vals), sum_hs=s, source=f"в архиве {len(vals)} из 24 ч")
        else:
            row["source"] = "нет данных"
        rows.append(row)
        d += timedelta(days=1)
    return rows
