"""Kirka farm API: today panel, devices, ASIC card, errors, export.

Access: admins (owner Telegram IDs) for now. Roles per client come later.
"""
from __future__ import annotations

import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_admin
from app.db import farm_models as fm
from app.db.session import get_db
from app.services.farm.classes import CLASSES

router = APIRouter(prefix="/farm", tags=["farm"], dependencies=[Depends(get_current_admin)])


def _refs(db: Session):
    sites = {s.id: s.name.strip() for s in db.scalars(select(fm.FarmSite))}
    conts = {c.id: (c.name.strip(), c.site_id) for c in db.scalars(select(fm.FarmContainer))}
    clients = {c.id: c.name.strip() for c in db.scalars(select(fm.FarmClient))}
    return sites, conts, clients


def _dev_row(d: fm.FarmDevice, sites, conts, clients) -> dict:
    return {
        "id": d.id, "mac": d.mac, "serial": d.serial, "num": d.user_number, "model": d.model,
        "site": sites.get(d.site_id), "site_id": d.site_id,
        "container": (conts.get(d.container_id) or (None,))[0], "container_id": d.container_id,
        "client": clients.get(d.client_id), "client_id": d.client_id,
        "status": d.status, "ip": d.ip, "worker": d.worker, "pool": d.pool_url,
        "mode": d.power_mode, "ths": d.hashrate_ths, "ths5m": d.hashrate_5m_ths, "nominal": d.nominal_ths,
        "w": d.power_w, "temp": d.temp_env, "uptime": d.uptime_sec, "alarms": d.alarms or [],
        "level": d.worst_level, "tags": [t.get("label") for t in (d.tags or []) if t.get("label")],
        "since": d.status_since.isoformat() if d.status_since else None,
    }


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    sites, conts, clients = _refs(db)
    devs = list(db.scalars(select(fm.FarmDevice)))
    last = db.scalar(select(fm.FarmSyncRun).where(fm.FarmSyncRun.ok.is_(True)).order_by(fm.FarmSyncRun.id.desc()).limit(1))
    tot = Counter(d.status for d in devs)
    hr = sum(d.hashrate_ths or 0 for d in devs if d.status == "working")
    nom = sum(d.nominal_ths or 0 for d in devs if d.status == "working")
    kw = sum((d.power_w or 0) for d in devs) / 1000
    by_cont = defaultdict(lambda: dict(total=0, working=0, offline=0, suspended=0, ths=0.0, kw=0.0, alarms=0, temp=None))
    by_client = defaultdict(lambda: dict(total=0, working=0, offline=0, ths=0.0))
    cls = Counter()
    for d in devs:
        c = by_cont[d.container_id]
        c["total"] += 1
        c[d.status if d.status in ("working", "offline", "suspended") else "offline"] += 1
        c["ths"] += d.hashrate_ths or 0
        c["kw"] += (d.power_w or 0) / 1000
        if d.alarm_count:
            c["alarms"] += 1
        if d.temp_env is not None:
            c["temp"] = max(c["temp"] or -99, d.temp_env)
        k = by_client[d.client_id]
        k["total"] += 1
        k["working"] += d.status == "working"
        k["offline"] += d.status == "offline"
        k["ths"] += d.hashrate_ths or 0
        for a in d.alarms or []:
            cls[a.get("cls") or "other"] += 1
    containers = []
    for cid, v in by_cont.items():
        name, sid = conts.get(cid, ("Без контейнера", None))
        containers.append(dict(id=cid, name=name, site=sites.get(sid), site_id=sid, **{k: (round(x, 2) if isinstance(x, float) else x) for k, x in v.items()}))
    containers.sort(key=lambda x: (x["site"] or "", x["name"]))
    cl = [dict(id=k, name=clients.get(k, "Без клиента"), **{a: (round(b, 2) if isinstance(b, float) else b) for a, b in v.items()}) for k, v in by_client.items()]
    cl.sort(key=lambda x: -x["total"])
    incidents = [dict(id=i.id, scope=i.scope_name, started=i.started_at.isoformat(), ended=i.ended_at.isoformat() if i.ended_at else None, total=i.devices_total, down=i.devices_down)
                 for i in db.scalars(select(fm.FarmIncident).order_by(fm.FarmIncident.started_at.desc()).limit(20))]
    return {
        "synced_at": last.finished_at.isoformat() if last else None,
        "total": len(devs), "working": tot.get("working", 0), "offline": tot.get("offline", 0),
        "suspended": tot.get("suspended", 0), "ths": round(hr, 1), "nominal_ths": round(nom, 1),
        "efficiency": round(hr / nom * 100, 1) if nom else None, "kw": round(kw, 1),
        "with_alarms": sum(1 for d in devs if d.alarm_count),
        "classes": [dict(key=k, label=CLASSES[k][0], count=cls.get(k, 0)) for k in sorted(CLASSES, key=lambda x: CLASSES[x][1])],
        "sites": [dict(id=k, name=v) for k, v in sites.items()],
        "containers": containers, "clients": cl, "incidents": incidents,
    }


def _filtered(db: Session, q, status, site, container, client, model, cls, only_alarms):
    st = select(fm.FarmDevice)
    if status:
        st = st.where(fm.FarmDevice.status.in_(status.split(",")))
    if site:
        st = st.where(fm.FarmDevice.site_id.in_([int(x) for x in site.split(",")]))
    if container:
        st = st.where(fm.FarmDevice.container_id.in_([int(x) for x in container.split(",")]))
    if client:
        st = st.where(fm.FarmDevice.client_id.in_([int(x) for x in client.split(",")]))
    if model:
        st = st.where(fm.FarmDevice.model.in_(model.split(",")))
    if only_alarms:
        st = st.where(fm.FarmDevice.alarm_count > 0)
    if q:
        like = f"%{q.strip()}%"
        st = st.where(or_(fm.FarmDevice.ip.ilike(like), fm.FarmDevice.mac.ilike(like), fm.FarmDevice.serial.ilike(like),
                          fm.FarmDevice.worker.ilike(like), fm.FarmDevice.user_number.ilike(like), fm.FarmDevice.model.ilike(like)))
    devs = list(db.scalars(st))
    if cls:
        want = set(cls.split(","))
        devs = [d for d in devs if any((a.get("cls") in want) for a in (d.alarms or []))]
    return devs


@router.get("/devices")
def devices(
    q: str | None = None, status: str | None = None, site: str | None = None, container: str | None = None,
    client: str | None = None, model: str | None = None, cls: str | None = None, alarms: bool = False,
    sort: str = "container", limit: int = Query(200, le=6000), offset: int = 0, db: Session = Depends(get_db),
):
    sites, conts, clients = _refs(db)
    devs = _filtered(db, q, status, site, container, client, model, cls, alarms)
    rows = [_dev_row(d, sites, conts, clients) for d in devs]
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    rows.sort(key=lambda r: (r.get(key) is None, r.get(key) if r.get(key) is not None else 0) if key in ("ths", "temp", "w", "uptime", "level") else (str(r.get(key) or ""), str(r.get("ip") or "")), reverse=desc)
    facets = {
        "models": Counter(r["model"] for r in rows if r["model"]).most_common(),
        "status": Counter(r["status"] for r in rows).most_common(),
    }
    return {"total": len(rows), "items": rows[offset:offset + limit], "facets": facets}


@router.get("/devices/{dev_id}")
def device_card(dev_id: int, db: Session = Depends(get_db)):
    d = db.get(fm.FarmDevice, dev_id)
    if d is None:
        raise HTTPException(404, "not found")
    sites, conts, clients = _refs(db)
    ev = db.scalars(select(fm.FarmDeviceEvent).where(fm.FarmDeviceEvent.device_id == dev_id).order_by(fm.FarmDeviceEvent.at.desc()).limit(300))
    since = datetime.now(timezone.utc) - timedelta(hours=72)
    hourly = db.scalars(select(fm.FarmDeviceHourly).where(fm.FarmDeviceHourly.device_id == dev_id, fm.FarmDeviceHourly.hour >= since).order_by(fm.FarmDeviceHourly.hour))
    row = _dev_row(d, sites, conts, clients)
    from app.db import pool_models as pm

    pw = db.scalar(select(pm.PoolWorker).where(pm.PoolWorker.full_name == (d.worker or "").lower()).limit(1)) if d.worker else None
    row["pool_worker"] = ({"status": pw.status, "ths_10m": round((pw.hs_10m or 0) / 1e12, 2), "ths_1h": round((pw.hs_1h or 0) / 1e12, 2),
                           "ths_1d": round((pw.hs_1d or 0) / 1e12, 2), "reject_1h": pw.reject_1h, "online_7d_h": pw.online_7d_h,
                           "updated": pw.updated_at.isoformat() if pw.updated_at else None} if pw else None)
    row.update({
        "first_seen": d.first_seen_at.isoformat() if d.first_seen_at else None,
        "last_seen": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "online_total": d.online_sec_total, "pools": d.pools or [], "tags_full": d.tags or [],
        "events": [dict(at=e.at.isoformat(), kind=e.kind, old=e.old, new=e.new, code=e.code, cls=e.cls, level=e.level, author=e.author) for e in ev],
        "hourly": [dict(h=h.hour.isoformat(), ths=round(h.hashrate_sum / h.samples, 2) if h.samples else 0, up=round(h.online_samples / h.samples * 100) if h.samples else 0, t=h.temp_max) for h in hourly],
    })
    return row


@router.get("/errors")
def errors(
    q: str | None = None, site: str | None = None, container: str | None = None, client: str | None = None,
    model: str | None = None, cls: str | None = None, level: int | None = None, group: str = "code",
    db: Session = Depends(get_db),
):
    sites, conts, clients = _refs(db)
    devs = _filtered(db, q, None, site, container, client, model, None, True)
    want = set(cls.split(",")) if cls else None
    rows = []
    for d in devs:
        base = _dev_row(d, sites, conts, clients)
        for a in d.alarms or []:
            if want and a.get("cls") not in want:
                continue
            if level is not None and (a.get("level") or 0) < level:
                continue
            rows.append({**{k: base[k] for k in ("id", "model", "site", "container", "client", "ip", "worker", "serial", "status", "temp")},
                         "code": a.get("code"), "msg": a.get("msg"), "cls": a.get("cls"), "cls_label": CLASSES.get(a.get("cls") or "other", ("Прочее",))[0],
                         "lvl": a.get("level"), "since": a.get("since")})
    groups = defaultdict(lambda: dict(count=0, devices=set()))
    gkey = {"code": lambda r: f"{r['code']} · {r['msg']}", "cls": lambda r: r["cls_label"], "container": lambda r: r["container"] or "—",
            "client": lambda r: r["client"] or "Без клиента", "model": lambda r: r["model"] or "—"}.get(group, lambda r: r["code"])
    for r in rows:
        g = groups[gkey(r)]
        g["count"] += 1
        g["devices"].add(r["id"])
    grp = sorted(({"key": k, "count": v["count"], "devices": len(v["devices"])} for k, v in groups.items()), key=lambda x: -x["count"])
    rows.sort(key=lambda r: (-(r["lvl"] or 0), -(r["since"] or 0)))
    return {"total": len(rows), "devices": len({r["id"] for r in rows}), "groups": grp, "items": rows}


ST_RU = {"working": "Работает", "offline": "Не работает", "suspended": "Пауза", "unknown": "Нет данных"}
LV_RU = {0: "Инфо", 1: "Предупреждение", 2: "Опасно", 3: "Критично"}


def _place_texts(db: Session, devs: list[fm.FarmDevice]) -> dict[int, str]:
    by = defaultdict(list)
    for d in devs:
        if d.container_id:
            by[d.container_id].append(d.id)
    out: dict[int, str] = {}
    for cid in by:
        c = db.get(fm.FarmContainer, cid)
        allc = list(db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.container_id == cid)))
        L = _layout_for(c, allc)
        kind = L["layout"]["kind"]
        for did, (si, r, sh, sl, num) in L["placed"].items():
            sec = L["layout"]["sections"][si]
            if kind == "bath":
                out[did] = f"№ {num}"
            elif kind == "hydro":
                out[did] = f"{sec['title']} · полка {sh + 1} · место {num}"
            else:
                out[did] = f"стеллаж {(sec.get('rack_from') or 1) + r} · полка {sh + 1} · место {num}"
    return out


def _xlsx(name: str, sheets: list[tuple[str, list[str], list[list], tuple]]) -> StreamingResponse:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for title, head, rows, widths in sheets:
        ws = wb.create_sheet(title)
        ws.append(head)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="01696F")
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 30
        bad = PatternFill("solid", fgColor="FBE3E4")
        for r in rows:
            ws.append(r)
            if "Не работает" in r:
                for c in ws[ws.max_row]:
                    c.fill = bad
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _devices_xlsx(db: Session, devs: list[fm.FarmDevice], prefix: str, only_errors: bool = False):
    sites, conts, clients = _refs(db)
    places = _place_texts(db, devs)
    msk = timezone(timedelta(hours=3))
    ts = lambda x: datetime.fromtimestamp(x, msk).strftime("%d.%m.%Y %H:%M") if x else ""  # noqa: E731
    order = sorted(devs, key=lambda d: ((conts.get(d.container_id) or ("",))[0], places.get(d.id, "~"), d.ip or ""))
    rows, err = [], []
    for d in order:
        cont = (conts.get(d.container_id) or (None,))[0]
        pct = round(d.hashrate_ths / d.nominal_ths * 100) if d.status == "working" and d.nominal_ths and d.hashrate_ths else None
        al = d.alarms or []
        rows.append([sites.get(d.site_id), cont, places.get(d.id, ""), d.model, ST_RU.get(d.status, d.status),
                     round(d.hashrate_ths, 2) if d.status == "working" and d.hashrate_ths is not None else None, pct,
                     d.temp_env, d.power_w, d.ip, d.worker, clients.get(d.client_id), d.serial, d.mac,
                     len(al), "; ".join(a.get("msg") or "" for a in al)])
        for a in al:
            err.append([cont, places.get(d.id, ""), d.model, d.ip, d.worker, ST_RU.get(d.status, d.status),
                        a.get("msg"), a.get("code"), CLASSES.get(a.get("cls") or "other", ("Прочее",))[0],
                        LV_RU.get(a.get("level"), a.get("level")), ts(a.get("since")), clients.get(d.client_id)])
    dh = ["Площадка", "Контейнер", "Место", "Модель", "Статус", "TH/s", "% от заводского", "t°, C", "Вт", "IP", "Воркер",
          "Клиент", "Серийный №", "MAC", "Ошибок", "Ошибки"]
    eh = ["Контейнер", "Место", "Модель", "IP", "Воркер", "Статус", "Ошибка", "Код", "Класс", "Важность", "С какого времени (МСК)", "Клиент"]
    sheets = [] if only_errors else [(f"Асики ({len(rows)})", dh, rows, (16, 34, 30, 22, 13, 9, 10, 7, 7, 14, 40, 22, 22, 18, 8, 40))]
    sheets.append((f"Ошибки ({len(err)})", eh, err, (34, 30, 22, 14, 40, 13, 30, 8, 14, 14, 18, 22)))
    return _xlsx(f"kirka_{prefix}_{datetime.now(msk).strftime('%Y%m%d_%H%M')}.xlsx", sheets)


@router.post("/devices/export.xlsx")
def devices_export(body: dict, db: Session = Depends(get_db)):
    """Exactly the given device ids (selection or current list)."""
    try:
        ids = sorted({int(x) for x in (body.get("ids") or [])})
    except (TypeError, ValueError):
        raise HTTPException(400, "ids — список чисел")
    if not ids:
        raise HTTPException(400, "Ничего не выбрано")
    devs = list(db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.id.in_(ids))))
    return _devices_xlsx(db, devs, "asics")


@router.get("/errors/export.xlsx")
def errors_export(site: str | None = None, container: str | None = None, client: str | None = None,
                  model: str | None = None, cls: str | None = None, ids: str | None = None, db: Session = Depends(get_db)):
    devs = _filtered(db, None, None, site, container, client, model, cls, True)
    if ids:
        keep = {int(x) for x in ids.split(",") if x}
        devs = [d for d in devs if d.id in keep]
    return _devices_xlsx(db, devs, "errors")


@router.get("/containers/{cid}/history")
def container_history(cid: int, hours: int = 24, db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.scalars(select(fm.FarmContainerSample).where(fm.FarmContainerSample.container_id == cid, fm.FarmContainerSample.at >= since).order_by(fm.FarmContainerSample.at))
    return [dict(at=r.at.isoformat(), online=r.online, offline=r.offline, total=r.total, ths=r.hashrate_ths, kw=r.power_kw) for r in rows]


@router.get("/history")
def farm_history(hours: int = 24, site: str | None = None, db: Session = Depends(get_db)):
    """Ферма по времени, усреднённая по интервалам (~300 точек), как графики пулов."""
    hours = max(1, min(int(hours), 24 * 31))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    S = fm.FarmContainerSample
    q = (select(S.at, func.sum(S.hashrate_ths), func.sum(S.online), func.sum(S.total), func.sum(S.power_kw))
         .where(S.at >= since))
    if site:
        q = q.join(fm.FarmContainer, fm.FarmContainer.id == S.container_id).where(fm.FarmContainer.site_id == int(site))
    q = q.group_by(S.at).order_by(S.at)
    step = max(60, hours * 3600 // 300)
    buckets: dict[int, list] = {}
    for a, h, o, t, k in db.execute(q):
        ts = a.replace(tzinfo=a.tzinfo or timezone.utc).timestamp()
        b = buckets.setdefault(int(ts // step), [0, 0.0, 0.0, 0.0, 0.0])
        b[0] += 1
        b[1] += h or 0
        b[2] += o or 0
        b[3] += t or 0
        b[4] += k or 0
    out = []
    for key in sorted(buckets):
        n, h, o, t, k = buckets[key]
        at = datetime.fromtimestamp(key * step + step / 2, timezone.utc)
        out.append(dict(at=at.isoformat(), ths=round(h / n, 1), online=round(o / n), total=round(t / n), kw=round(k / n, 1)))
    return out


# ---------- container layout (racks / shelves / places) ----------
def _layout_for(c: fm.FarmContainer, devs: list[fm.FarmDevice]) -> dict:
    from app.services.farm import layout as L

    rows = [{"id": d.id, "ip": d.ip, "worker": d.worker, "model": d.model, "status": d.status} for d in devs]
    spec = c.layout if isinstance(c.layout, dict) and c.layout.get("sections") else L.infer(c.name, rows)
    pl = L.place(spec, rows)
    cap = sum(s["racks"] * s["shelves"] * s["places"] if s.get("by") == "order" or not s.get("last")
              else min(s["racks"] * s["shelves"] * s["places"], s["last"] - s.get("first", 1) + 1) for s in spec["sections"])
    return {"id": c.id, "name": c.name, "layout": spec, "capacity": cap, "placed": pl["placed"],
            "unplaced": pl["unplaced"], "doubles": pl["doubles"]}


@router.get("/layouts")
def layouts(site: str | None = None, db: Session = Depends(get_db)):
    st = select(fm.FarmContainer)
    if site:
        st = st.where(fm.FarmContainer.site_id.in_([int(x) for x in site.split(",")]))
    conts = list(db.scalars(st))
    by = defaultdict(list)
    for d in db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.container_id.in_([c.id for c in conts]))):
        by[d.container_id].append(d)
    return {"items": [_layout_for(c, by[c.id]) for c in conts]}


@router.get("/containers/{cid}/layout")
def container_layout(cid: int, db: Session = Depends(get_db)):
    c = db.get(fm.FarmContainer, cid)
    if not c:
        raise HTTPException(404, "Нет такого контейнера")
    devs = list(db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.container_id == cid)))
    return _layout_for(c, devs)


@router.put("/containers/{cid}/layout")
def container_layout_save(cid: int, body: dict, db: Session = Depends(get_db)):
    from app.services.farm import layout as L

    c = db.get(fm.FarmContainer, cid)
    if not c:
        raise HTTPException(404, "Нет такого контейнера")
    c.layout = None if body.get("reset") else L.clean(body)
    db.commit()
    return container_layout(cid, db)
