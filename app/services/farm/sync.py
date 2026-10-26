"""CROFT → Kirka sync: upsert devices, write history events, hourly buckets,
container samples and mass-outage incidents."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import farm_models as fm
from app.services.farm.classes import classify
from app.services.farm.croft import CroftClient
from app.services.farm.settings import get_settings, low_hash_alarm

log = logging.getLogger("kirka.farm.sync")

INCIDENT_SHARE = 0.8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_ref(db: Session, model, cache: dict, ext: dict | None, **extra):
    if not ext or not ext.get("uniqId"):
        return None
    eid = ext["uniqId"]
    if eid in cache:
        return cache[eid]
    obj = db.scalar(select(model).where(model.ext_id == eid))
    name = (ext.get("name") or "").strip() or eid[:8]
    if obj is None:
        obj = model(ext_id=eid, name=name, **extra)
        db.add(obj)
        db.flush()
    else:
        obj.name = name
        for k, v in extra.items():
            setattr(obj, k, v)
    if hasattr(obj, "description") and "description" in ext:
        obj.description = ext.get("description")
    cache[eid] = obj.id
    return obj.id


def _attr(d: dict, key: str):
    for a in d.get("attributes") or []:
        if a.get("key") == key:
            return a.get("value")
    return None


def _alarms(d: dict) -> list[dict]:
    out = []
    for a in d.get("alarms") or []:
        out.append({
            "code": str(a.get("code")), "msg": a.get("message"), "level": a.get("level"),
            "cls": classify(a), "since": a.get("createdAt"),
        })
    return out


def _ths(mhs):
    return round(float(mhs) / 1e6, 3) if mhs is not None else None


def run_sync(db: Session, client: CroftClient) -> fm.FarmSyncRun:
    run = fm.FarmSyncRun(started_at=_now())
    db.add(run)
    db.commit()
    try:
        groups = client.view_groups()
        customer = next((g["customer"]["uniqId"] for g in groups if g.get("customer")), None)
        if not customer:
            raise RuntimeError("CROFT: no customer view")
        for g in groups:
            hc = g.get("hosterClient")
            if hc:
                _upsert_ref(db, fm.FarmClient, {}, hc, comment=hc.get("comment"))
        raw = client.devices(customer)
        n_events = _apply(db, raw)
        run.devices = len(raw)
        run.events = n_events
        run.ok = True
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("sync failed")
        run.error = str(e)[:2000]
    run.finished_at = _now()
    db.add(run)
    db.commit()
    return run


def _apply(db: Session, raw: list[dict]) -> int:
    now = _now()
    hour = now.replace(minute=0, second=0, microsecond=0)
    sites, conts, clients = {}, {}, {}
    existing = {d.ext_id: d for d in db.scalars(select(fm.FarmDevice))}
    # Low hashrate считаем по среднему за последний час (прошлый + текущий час из почасовой таблицы).
    low_pct = float(get_settings(db)["lowhash_pct"])
    H = fm.FarmDeviceHourly
    hr_acc = {dev_id: (hs or 0.0, n or 0) for dev_id, hs, n in db.execute(
        select(H.device_id, func.sum(H.hashrate_sum), func.sum(H.online_samples))
        .where(H.hour >= hour - timedelta(hours=1)).group_by(H.device_id))}
    events: list[dict] = []
    hourly: list[dict] = []
    per_cont: dict[int, dict] = {}
    seen = set()

    for d in raw:
        site_id = _upsert_ref(db, fm.FarmSite, sites, d.get("platform"))
        cont_id = _upsert_ref(db, fm.FarmContainer, conts, d.get("container"), site_id=site_id)
        cl_id = _upsert_ref(db, fm.FarmClient, clients, d.get("hosterClient"))
        workers = [w for w in d.get("workers") or [] if w and w != "DevFee"]
        pools = d.get("pools") or []
        alive = next((p for p in pools if p.get("alive")), pools[0] if pools else None)
        alarms = _alarms(d)
        st = d.get("status") or "unknown"
        prev_dev = existing.get(d["uniqId"])
        if st == "working":
            cur = _ths(d.get("hashRateMHS"))
            hs, n = hr_acc.get(prev_dev.id, (0.0, 0)) if prev_dev else (0.0, 0)
            avg = ((hs + (cur or 0.0)) / (n + 1)) if cur is not None else None
            prev_low = next((a for a in (prev_dev.alarms or []) if a.get("code") == "LOWHASH"), None) if prev_dev else None
            low = low_hash_alarm(prev_low, avg, _ths(d.get("factoryHashRateMHS")), low_pct, now.isoformat())
            if low:
                alarms.append(low)
        vals = dict(
            mac=d.get("mac"), serial=_attr(d, "serialNumber"), user_number=_attr(d, "userNumber"),
            model=d.get("type"), algo=d.get("hashAlgo"), site_id=site_id, container_id=cont_id,
            client_id=cl_id, status=st, in_operation=bool(d.get("inOperation", True)),
            ip=d.get("ip") or None, worker=workers[0] if workers else None,
            pool_url=(alive or {}).get("url"), power_mode=d.get("powerMode"),
            hashrate_ths=_ths(d.get("hashRateMHS")), hashrate_5m_ths=_ths(d.get("hashRate5mMHS")),
            nominal_ths=_ths(d.get("factoryHashRateMHS")), power_w=d.get("powerWH"),
            temp_env=d.get("temperatureEnv"), uptime_sec=d.get("elapsedSec"),
            alarm_count=len(alarms), worst_level=max([a["level"] or 0 for a in alarms], default=-1),
            alarms=alarms, tags=[{"label": t.get("label"), "color": t.get("color"), "desc": t.get("description")} for t in d.get("tags") or []],
            pools=[{"url": p.get("url"), "user": p.get("user"), "alive": p.get("alive")} for p in pools],
            last_seen_at=now if st == "working" else None, updated_at=now,
        )
        dev = existing.get(d["uniqId"])
        if dev is None:
            dev = fm.FarmDevice(ext_id=d["uniqId"], status_since=now, first_seen_at=now, **vals)
            db.add(dev)
            db.flush()
            events.append(dict(device_id=dev.id, at=now, kind="seen", new=st, author="croft"))
            for a in alarms:
                events.append(dict(device_id=dev.id, at=now, kind="alarm_on", code=a["code"], cls=a["cls"], level=a["level"], new=a["msg"], author="croft"))
        else:
            prev_status = dev.status
            if prev_status == "working" and dev.updated_at:
                dt = (now - dev.updated_at).total_seconds()
                if 0 < dt < 900:
                    dev.online_sec_total = (dev.online_sec_total or 0) + int(dt)
            for kind, attr in (("status", "status"), ("worker", "worker"), ("pool", "pool_url"), ("ip", "ip"), ("container", "container_id"), ("client", "client_id")):
                old, new = getattr(dev, attr), vals[attr]
                if old != new and not (attr == "ip" and new is None):
                    events.append(dict(device_id=dev.id, at=now, kind=kind, old=None if old is None else str(old), new=None if new is None else str(new), author="croft"))
            if prev_status != st:
                dev.status_since = now
            old_codes = {a["code"]: a for a in (dev.alarms or [])}
            new_codes = {a["code"]: a for a in alarms}
            for c, a in new_codes.items():
                if c not in old_codes:
                    events.append(dict(device_id=dev.id, at=now, kind="alarm_on", code=c, cls=a["cls"], level=a["level"], new=a["msg"], author="croft"))
            for c, a in old_codes.items():
                if c not in new_codes:
                    events.append(dict(device_id=dev.id, at=now, kind="alarm_off", code=c, cls=a.get("cls"), level=a.get("level"), old=a.get("msg"), author="croft"))
            if vals["last_seen_at"] is None:
                vals.pop("last_seen_at")
            for k, v in vals.items():
                setattr(dev, k, v)
        seen.add(dev.id)
        on = st == "working"
        hourly.append(dict(device_id=dev.id, hour=hour, samples=1, online_samples=1 if on else 0,
                           hashrate_sum=vals["hashrate_ths"] or 0.0, power_sum=float(vals["power_w"] or 0.0),
                           temp_max=vals["temp_env"]))
        if cont_id:
            c = per_cont.setdefault(cont_id, dict(total=0, online=0, offline=0, hr=0.0, kw=0.0))
            c["total"] += 1
            c["online"] += 1 if on else 0
            c["offline"] += 1 if st == "offline" else 0
            c["hr"] += vals["hashrate_ths"] or 0.0
            c["kw"] += float(vals["power_w"] or 0.0) / 1000

    if events:
        db.bulk_insert_mappings(fm.FarmDeviceEvent, events)
    if hourly and db.bind.dialect.name == "postgresql":
        _upsert_hourly(db, hourly)
    db.bulk_insert_mappings(fm.FarmContainerSample, [
        dict(container_id=k, at=now, total=v["total"], online=v["online"], offline=v["offline"],
             hashrate_ths=round(v["hr"], 3), power_kw=round(v["kw"], 2)) for k, v in per_cont.items()])
    _detect_incidents(db, now, per_cont)
    db.commit()
    return len(events)


def _detect_incidents(db: Session, now: datetime, per_cont: dict[int, dict]) -> None:
    """Авария: ≥80% of a container's in-operation devices went offline within 5 min."""
    five = now - timedelta(minutes=5)
    for cid, cur in per_cont.items():
        if cur["total"] < 5:
            continue
        open_inc = db.scalar(select(fm.FarmIncident).where(fm.FarmIncident.scope == "container", fm.FarmIncident.scope_id == cid, fm.FarmIncident.ended_at.is_(None)))
        share = cur["offline"] / cur["total"]
        if open_inc is None:
            prev = db.scalar(select(fm.FarmContainerSample).where(fm.FarmContainerSample.container_id == cid, fm.FarmContainerSample.at <= five).order_by(fm.FarmContainerSample.at.desc()).limit(1))
            if prev is None:
                continue
            newly = cur["offline"] - prev.offline
            if share >= INCIDENT_SHARE and newly >= INCIDENT_SHARE * cur["total"] * 0.5:
                c = db.get(fm.FarmContainer, cid)
                db.add(fm.FarmIncident(scope="container", scope_id=cid, scope_name=c.name if c else str(cid), started_at=now, devices_total=cur["total"], devices_down=cur["offline"]))
        elif share < 0.2:
            open_inc.ended_at = now


def _upsert_hourly(db: Session, rows: list[dict]) -> None:
    from sqlalchemy import func as F
    t = fm.FarmDeviceHourly.__table__
    for i in range(0, len(rows), 1000):
        ins = pg_insert(t).values(rows[i:i + 1000])
        ex = ins.excluded
        db.execute(ins.on_conflict_do_update(
            constraint="uq_farm_hourly_dev_hour",
            set_={
                "samples": t.c.samples + 1,
                "online_samples": t.c.online_samples + ex.online_samples,
                "hashrate_sum": t.c.hashrate_sum + ex.hashrate_sum,
                "power_sum": t.c.power_sum + ex.power_sum,
                "temp_max": F.greatest(t.c.temp_max, ex.temp_max),
            },
        ))
