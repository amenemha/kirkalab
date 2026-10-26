"""Kirka energy API: почасовки, счётчики, показания, дерево объекта, разъёмы."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_admin
from app.db import energy_models as em
from app.db import farm_models as fm
from app.db.session import get_db
from app.services.energy.hourly_import import MSK, _aware, import_hourly
from app.services.energy.plugs import plug_for

router = APIRouter(prefix="/energy", tags=["energy"], dependencies=[Depends(get_current_admin)])

MAX_FILE = 20 * 1024 * 1024


def _meter(m: em.EnergyMeter) -> dict:
    return {"id": m.id, "serial": m.serial, "name": m.name, "substation": m.substation, "feeder": m.feeder,
            "node_id": m.node_id, "site_id": m.site_id, "kind": m.kind, "ratio": m.ratio, "loss_pct": m.loss_pct,
            "const_loss": m.const_loss, "measure": m.measure, "group": m.group}


def _day(s: str | None, name: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=MSK)
    except ValueError as e:
        raise HTTPException(422, f"{name}: нужен формат ГГГГ-ММ-ДД") from e


@router.post("/import")
async def energy_import(file: UploadFile = File(...), db: Session = Depends(get_db),
                        admin=Depends(get_current_admin)):
    data = await file.read()
    if not data:
        raise HTTPException(422, "Пустой файл")
    if len(data) > MAX_FILE:
        raise HTTPException(413, "Файл больше 20 МБ")
    try:
        return import_hourly(db, data, file.filename or "", by_user=str(getattr(admin, "id", "") or ""))
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e


@router.get("/imports")
def energy_imports(db: Session = Depends(get_db)):
    out = []
    for i in db.scalars(select(em.EnergyImport).order_by(em.EnergyImport.id.desc()).limit(50)):
        out.append({"id": i.id, "filename": i.filename, "meters": i.meters, "rows": i.rows, "kwh": i.kwh,
                    "start": i.period_start.isoformat() if i.period_start else None,
                    "end": i.period_end.isoformat() if i.period_end else None,
                    "at": i.created_at.isoformat() if i.created_at else None})
    return out


@router.get("/meters")
def energy_meters(db: Session = Depends(get_db)):
    stats = {r[0]: r[1:] for r in db.execute(select(
        em.EnergyHourly.meter_id, func.count(), func.sum(em.EnergyHourly.kwh),
        func.min(em.EnergyHourly.hour), func.max(em.EnergyHourly.hour)).group_by(em.EnergyHourly.meter_id))}
    out = []
    for m in db.scalars(select(em.EnergyMeter).order_by(em.EnergyMeter.id)):
        d = _meter(m)
        n, s, a, b = stats.get(m.id, (0, 0, None, None))
        d.update(hours=n, kwh=round(float(s or 0), 3), start=_aware(a).isoformat() if a else None,
                 end=_aware(b).isoformat() if b else None)
        out.append(d)
    return out


@router.put("/meters/{mid}")
def energy_meter_put(mid: int, body: dict, db: Session = Depends(get_db)):
    m = db.get(em.EnergyMeter, mid)
    if not m:
        raise HTTPException(404, "Нет такого счётчика")
    allowed = {"name", "group", "kind", "node_id", "site_id", "ratio", "loss_pct", "const_loss"}
    bad = set(body) - allowed
    if bad:
        raise HTTPException(422, "Лишние поля: " + ", ".join(sorted(bad)))
    if "kind" in body and body["kind"] not in ("commercial", "technical"):
        raise HTTPException(422, "kind: commercial или technical")
    for k, v in body.items():
        setattr(m, k, v)
    db.commit()
    return _meter(m)


@router.post("/meters")
def energy_meter_add(body: dict, db: Session = Depends(get_db)):
    serial = str(body.get("serial") or "").strip()
    if not serial:
        raise HTTPException(422, "Нужен номер счётчика")
    if db.scalar(select(em.EnergyMeter).where(em.EnergyMeter.serial == serial)):
        raise HTTPException(409, "Такой счётчик уже есть")
    m = em.EnergyMeter(serial=serial, name=body.get("name") or serial, kind=body.get("kind") or "technical",
                       ratio=float(body.get("ratio") or 1), node_id=body.get("node_id"), site_id=body.get("site_id"),
                       group=body.get("group"))
    db.add(m)
    db.commit()
    return _meter(m)


@router.get("/hourly")
def energy_hourly(start: str | None = None, end: str | None = None, meter: str | None = None,
                  group: str = Query("hour", pattern="^(hour|day)$"), db: Session = Depends(get_db)):
    a, b = _day(start, "start"), _day(end, "end")
    if a and b and b < a:
        raise HTTPException(422, "Конец раньше начала")
    q = select(em.EnergyHourly)
    if a:
        q = q.where(em.EnergyHourly.hour >= a.astimezone(timezone.utc))
    if b:
        q = q.where(em.EnergyHourly.hour < (b + timedelta(days=1)).astimezone(timezone.utc))
    if meter:
        try:
            ids = [int(x) for x in meter.split(",") if x.strip()]
        except ValueError as e:
            raise HTTPException(422, "meter: список id через запятую") from e
        q = q.where(em.EnergyHourly.meter_id.in_(ids))
    buckets: dict[str, dict] = defaultdict(lambda: {"kwh": 0.0, "by": defaultdict(float)})
    for e in db.scalars(q):
        loc = _aware(e.hour).astimezone(MSK)
        key = loc.strftime("%Y-%m-%d") if group == "day" else loc.strftime("%Y-%m-%d %H:00")
        buckets[key]["kwh"] += e.kwh
        buckets[key]["by"][e.meter_id] += e.kwh
    series = [{"t": k, "kwh": round(v["kwh"], 3), "by": {str(i): round(x, 3) for i, x in v["by"].items()}}
              for k, v in sorted(buckets.items())]
    total = sum(s["kwh"] for s in series)
    peak = max(series, key=lambda s: s["kwh"]) if series else None
    hours = len(series) * (24 if group == "day" else 1)
    return {"group": group, "series": series, "total_kwh": round(total, 3),
            "avg_kw": round(total / hours, 1) if hours else None,
            "peak": peak and {"t": peak["t"], "kwh": peak["kwh"]}}


@router.post("/readings")
def energy_reading_add(body: dict, db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    m = db.get(em.EnergyMeter, int(body.get("meter_id") or 0))
    if not m:
        raise HTTPException(404, "Нет такого счётчика")
    try:
        value = float(str(body.get("value")).replace(",", "."))
    except (TypeError, ValueError) as e:
        raise HTTPException(422, "Показание — число") from e
    at = datetime.now(timezone.utc)
    if body.get("at"):
        try:
            at = datetime.fromisoformat(str(body["at"]))
            at = at if at.tzinfo else at.replace(tzinfo=MSK)
        except ValueError as e:
            raise HTTPException(422, "at: дата и время ISO") from e
    at = at.astimezone(timezone.utc)
    prev = db.scalar(select(em.EnergyReading).where(em.EnergyReading.meter_id == m.id, em.EnergyReading.at < at)
                     .order_by(em.EnergyReading.at.desc()).limit(1))
    if prev and value < prev.value:
        raise HTTPException(422, f"Показание меньше прошлого ({prev.value:g})")
    src = body.get("source") or "manual"
    if src not in ("manual", "photo"):
        raise HTTPException(422, "source: manual или photo")
    r = em.EnergyReading(meter_id=m.id, at=at, value=value, source=src, photo=body.get("photo"),
                         note=body.get("note"), by_user=str(getattr(admin, "id", "") or ""))
    db.add(r)
    db.commit()
    out = {"id": r.id, "meter_id": m.id, "value": value, "at": at.isoformat()}
    if prev:
        hrs = (at - _aware(prev.at)).total_seconds() / 3600
        delta = (value - prev.value) * (m.ratio or 1)
        out.update(delta_kwh=round(delta, 3), hours=round(hrs, 2), avg_kw=round(delta / hrs, 1) if hrs > 0 else None)
    return out


@router.get("/readings")
def energy_readings(meter: int, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(em.EnergyReading).where(em.EnergyReading.meter_id == meter)
                           .order_by(em.EnergyReading.at.desc()).limit(200)))
    return [{"id": r.id, "at": _aware(r.at).isoformat(), "value": r.value, "source": r.source, "photo": r.photo}
            for r in rows]


# ---- дерево объекта -------------------------------------------------------

def _node(n: em.EnergyNode) -> dict:
    return {"id": n.id, "site_id": n.site_id, "parent_id": n.parent_id, "kind": n.kind, "name": n.name,
            "phase": n.phase, "voltage_kv": n.voltage_kv, "container_id": n.container_id, "sort": n.sort,
            "meta": n.meta or {}}


@router.get("/nodes")
def energy_nodes(site: int | None = None, db: Session = Depends(get_db)):
    q = select(em.EnergyNode).order_by(em.EnergyNode.sort, em.EnergyNode.id)
    if site:
        q = q.where(em.EnergyNode.site_id == site)
    return [_node(n) for n in db.scalars(q)]


def _check_node(body: dict):
    if "kind" in body and body["kind"] not in em.NODE_KINDS:
        raise HTTPException(422, "kind: " + ", ".join(em.NODE_KINDS))
    if body.get("phase") and not set(str(body["phase"]).upper()) <= set("ABC123N"):
        raise HTTPException(422, "phase: A/B/C или 1/2/3")


@router.post("/nodes")
def energy_node_add(body: dict, db: Session = Depends(get_db)):
    if not body.get("name") or not body.get("kind"):
        raise HTTPException(422, "Нужны name и kind")
    _check_node(body)
    allowed = {"site_id", "parent_id", "kind", "name", "phase", "voltage_kv", "container_id", "sort", "meta"}
    n = em.EnergyNode(**{k: v for k, v in body.items() if k in allowed})
    db.add(n)
    db.commit()
    return _node(n)


@router.put("/nodes/{nid}")
def energy_node_put(nid: int, body: dict, db: Session = Depends(get_db)):
    n = db.get(em.EnergyNode, nid)
    if not n:
        raise HTTPException(404, "Нет такого узла")
    _check_node(body)
    if body.get("parent_id") == nid:
        raise HTTPException(422, "Узел не может быть родителем сам себе")
    for k in ("parent_id", "kind", "name", "phase", "voltage_kv", "container_id", "sort", "meta", "site_id"):
        if k in body:
            setattr(n, k, body[k])
    db.commit()
    return _node(n)


@router.delete("/nodes/{nid}")
def energy_node_del(nid: int, db: Session = Depends(get_db)):
    n = db.get(em.EnergyNode, nid)
    if not n:
        raise HTTPException(404, "Нет такого узла")
    ids, frontier = {nid}, [nid]
    while frontier:
        kids = list(db.scalars(select(em.EnergyNode.id).where(em.EnergyNode.parent_id.in_(frontier))))
        frontier = [k for k in kids if k not in ids]
        ids.update(frontier)
    for i in ids:
        db.delete(db.get(em.EnergyNode, i))
    db.commit()
    return {"deleted": len(ids)}


# ---- разъёмы ----------------------------------------------------------------

@router.get("/plugs")
def energy_plugs(db: Session = Depends(get_db)):
    plugs = list(db.scalars(select(em.AsicPlug)))
    models = db.execute(select(fm.FarmDevice.model, func.count()).group_by(fm.FarmDevice.model)).all()
    rows = []
    for model, cnt in sorted(models, key=lambda r: -r[1]):
        rows.append({"model": model, "count": cnt, "plug": plug_for(model, plugs)})
    ref = [plug_for(p.pattern, [p]) for p in plugs]
    return {"models": rows, "reference": ref}
