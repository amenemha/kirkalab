"""Импорт почасовок энергокомпании (формат «Реализация, первичные данные»).

Шапка на первом листе: строки «Подстанция / Фидер / Счётчик / Знак(коэф) / % потерь /
Пост.потери / Измерение», дальше строки «ГГГГММДД | H:00 - H+1:00 | кВт·ч по счётчикам».
Остальные листы — продолжение без шапки. Столбцы «Итого» пропускаются.
Поддерживаются .xls (xlrd), .xlsx (openpyxl) и .csv (;). Время — московское.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import energy_models as em

MSK = timezone(timedelta(hours=3))
_HOUR = re.compile(r"^\s*(\d{1,2})\s*:\s*00\s*-\s*(\d{1,2})\s*:\s*00\s*$")


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _serial(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_rows(data: bytes, filename: str = "") -> list[list]:
    name = (filename or "").lower()
    if name.endswith(".csv") or (not name.endswith((".xls", ".xlsx")) and data[:2] not in (b"PK", b"\xd0\xcf")):
        txt = data.decode("utf-8-sig", errors="replace")
        return [r for r in csv.reader(io.StringIO(txt), delimiter=";")]
    if data[:2] == b"PK":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows: list[list] = []
        for ws in wb.worksheets:
            rows.extend([["" if c is None else c for c in r] for r in ws.iter_rows(values_only=True)])
        return rows
    import xlrd
    wb = xlrd.open_workbook(file_contents=data)
    rows = []
    for sh in wb.sheets():
        rows.extend([sh.row_values(r) for r in range(sh.nrows)])
    return rows


def parse(rows: list[list]) -> dict:
    """→ {"meters": [{serial, substation, feeder, ratio, loss_pct, const_loss, measure, col}],
          "data": {serial: {hour_utc: kwh}}, "total_file": float|None}"""
    head = None
    for i, r in enumerate(rows[:40]):
        if r and str(r[0]).strip().lower() == "дата":
            head = i
            break
    if head is None:
        raise ValueError("Не найдена шапка: первая ячейка должна быть «Дата»")
    hdr = [list(rows[head + k]) if head + k < len(rows) else [] for k in range(7)]
    width = max(len(r) for r in hdr)
    for r in hdr:
        r.extend([""] * (width - len(r)))
    meters, sub = [], ""
    for c in range(2, width):
        if str(hdr[0][c]).strip():
            sub = str(hdr[0][c]).strip()
        feeder = str(hdr[1][c]).strip()
        serial = _serial(hdr[2][c])
        if not serial or "итого" in feeder.lower() or "итого" in sub.lower() or "итого" in serial.lower():
            continue
        meters.append(dict(serial=serial, substation=sub if "итого" not in sub.lower() else "", feeder=feeder,
                           ratio=_num(hdr[3][c]) or 1.0, loss_pct=_num(hdr[4][c]) or 0.0,
                           const_loss=_num(hdr[5][c]) or 0.0, measure=str(hdr[6][c]).strip() or "A+", col=c))
    if not meters:
        raise ValueError("В шапке нет счётчиков")
    data: dict[str, dict[datetime, float]] = {m["serial"]: {} for m in meters}
    total_file = None
    for r in rows[head + 7:]:
        if not r:
            continue
        d0 = _serial(r[0]) if len(r) > 0 else ""
        if d0.lower() == "итого":
            vals = [_num(r[m["col"]]) for m in meters if m["col"] < len(r)]
            total_file = sum(v for v in vals if v is not None)
            continue
        if not re.fullmatch(r"\d{8}", d0) or len(r) < 2:
            continue
        mh = _HOUR.match(str(r[1]))
        if not mh:
            continue
        h = int(mh.group(1))
        loc = datetime.strptime(d0, "%Y%m%d").replace(hour=h, tzinfo=MSK)
        utc = loc.astimezone(timezone.utc)
        for m in meters:
            v = _num(r[m["col"]]) if m["col"] < len(r) else None
            if v is not None:
                data[m["serial"]][utc] = v
    return {"meters": meters, "data": data, "total_file": total_file}


def import_hourly(db: Session, data: bytes, filename: str = "", by_user: str | None = None) -> dict:
    p = parse(read_rows(data, filename))
    imp = em.EnergyImport(filename=filename or None, by_user=by_user)
    db.add(imp)
    db.flush()
    hours_all: list[datetime] = []
    rows = kwh = 0
    new_m = 0
    for m in p["meters"]:
        meter = db.scalar(select(em.EnergyMeter).where(em.EnergyMeter.serial == m["serial"]))
        if meter is None:
            meter = em.EnergyMeter(serial=m["serial"], name=m["feeder"] or m["serial"])
            db.add(meter)
            new_m += 1
        meter.substation = m["substation"] or meter.substation
        meter.feeder = m["feeder"] or meter.feeder
        meter.ratio, meter.loss_pct, meter.const_loss, meter.measure = m["ratio"], m["loss_pct"], m["const_loss"], m["measure"]
        db.flush()
        series = p["data"][m["serial"]]
        if not series:
            continue
        existing = {(_aware(e.hour)): e for e in db.scalars(select(em.EnergyHourly).where(
            em.EnergyHourly.meter_id == meter.id,
            em.EnergyHourly.hour >= min(series), em.EnergyHourly.hour <= max(series)))}
        for hr, v in series.items():
            e = existing.get(hr)
            if e is None:
                db.add(em.EnergyHourly(meter_id=meter.id, hour=hr, kwh=v, source="file", import_id=imp.id))
            elif e.source != "manual":
                e.kwh, e.source, e.import_id = v, "file", imp.id
            rows += 1
            kwh += v
        hours_all.extend(series)
    imp.meters, imp.rows, imp.kwh = len(p["meters"]), rows, round(kwh, 3)
    if hours_all:
        imp.period_start, imp.period_end = min(hours_all), max(hours_all)
    db.commit()
    hrs = sorted(set(hours_all))
    gaps = 0
    if hrs:
        span = int((hrs[-1] - hrs[0]).total_seconds() // 3600) + 1
        gaps = span - len(hrs)
    check = None
    if p["total_file"] is not None:
        check = round(p["total_file"] - kwh, 3)
    return {"import_id": imp.id, "meters": len(p["meters"]), "new_meters": new_m, "rows": rows,
            "hours": len(hrs), "gaps": gaps, "kwh": round(kwh, 3), "diff_vs_file_total": check,
            "start": _msk(hrs[0]) if hrs else None, "end": _msk(hrs[-1]) if hrs else None}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _msk(d: datetime) -> str:
    return _aware(d).astimezone(MSK).strftime("%Y-%m-%d %H:00")
