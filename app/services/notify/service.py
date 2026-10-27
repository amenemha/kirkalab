"""Уведомления фермы в Telegram: сгруппированный дайджест изменений.

Не шлём каждое событие CROFT (их ~13 тыс. в сутки из-за «мигания»). Раз в
``every_min`` минут сравниваем текущее состояние с тем, о чём уже сообщили:

* «Нет связи»   — асик offline дольше ``hold_min`` минут и о нём ещё не писали;
* «Вернулись»   — о нём писали, а теперь он снова в работе;
* «Ошибки»      — активная ошибка уровня ≥ ``min_level`` держится ≥ ``hold_min``;
* «Low hashrate»— то же для класса hashrate;
* «Пул/воркер»  — события смены пула или воркера с прошлого дайджеста;
* «Вероятно, питание» — контейнер пропал целиком и разом (≥90% асиков за ~15 мин):
  приходит сразу, не дожидаясь сводки; если пропал весь КТП или фидер — так и пишем;
* утренняя сводка в ``daily_at`` (МСК).

Сон (тихие часы): ничего не шлём, изменения копятся и приходят одним
сообщением после сна. Исключение — массовое отключение (≥ ``wake_offline``
асиков разом), если включено «будить при аварии».

Настройки и состояние храним в farm_settings: ``notif:tg:<id>`` и
``notif_state:tg:<id>``.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import farm_models as fm
from app.services.farm.classes import CLASSES

log = logging.getLogger("kirka.notify")
MSK = timezone(timedelta(hours=3))

TYPES = {
    "power": "Вероятно, питание (контейнер пропал целиком)",
    "offline": "Нет связи",
    "online": "Вернулись в работу",
    "errors": "Новые ошибки",
    "lowhash": "Low hashrate",
    "pool": "Смена пула / воркера",
    "daily": "Утренняя сводка",
}
DEFAULTS: dict = {
    "enabled": True,
    "types": {"power": True, "offline": True, "online": True, "errors": True, "lowhash": True, "pool": False, "daily": True},
    "min_level": 2,  # 0 info, 1 warning, 2 danger, 3 critical
    "hold_min": 10,
    "every_min": 15,
    "sleep": {"on": True, "from": "23:00", "to": "08:00"},
    "wake_offline": 30,  # 0 — не будить
    "daily_at": "09:00",
    "max_lines": 10,
}
LIMITS = {"min_level": (0, 3), "hold_min": (0, 240), "every_min": (5, 720), "wake_offline": (0, 5000), "max_lines": (0, 40)}
LEVEL_NAMES = {0: "все, включая инфо", 1: "предупреждения и выше", 2: "серьёзные и критичные", 3: "только критичные"}


def _key(tg: int) -> str:
    return f"notif:tg:{int(tg)}"


def _skey(tg: int) -> str:
    return f"notif_state:tg:{int(tg)}"


def _merge(base: dict, over: dict | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k].update({kk: vv for kk, vv in v.items() if kk in out[k]})
        elif k in out:
            out[k] = v
    return out


def _hhmm(v: str) -> str:
    h, m = str(v).strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("время в формате ЧЧ:ММ")
    return f"{h:02d}:{m:02d}"


def get_prefs(db: Session, tg: int) -> dict:
    row = db.get(fm.FarmSetting, _key(tg))
    return _merge(DEFAULTS, row.value if row and isinstance(row.value, dict) else None)


def set_prefs(db: Session, tg: int, values: dict) -> dict:
    cur = get_prefs(db, tg)
    new = _merge(cur, values or {})
    for k, (lo, hi) in LIMITS.items():
        new[k] = int(new[k])
        if not lo <= new[k] <= hi:
            raise ValueError(f"{k}: допустимо {lo}–{hi}")
    new["enabled"] = bool(new["enabled"])
    new["types"] = {t: bool(new["types"].get(t)) for t in TYPES}
    new["sleep"] = {"on": bool(new["sleep"]["on"]), "from": _hhmm(new["sleep"]["from"]), "to": _hhmm(new["sleep"]["to"])}
    new["daily_at"] = _hhmm(new["daily_at"])
    row = db.get(fm.FarmSetting, _key(tg))
    if row is None:
        db.add(fm.FarmSetting(key=_key(tg), value=new))
    else:
        row.value = new
    db.commit()
    return new


def _state(db: Session, tg: int) -> dict:
    row = db.get(fm.FarmSetting, _skey(tg))
    return dict(row.value) if row and isinstance(row.value, dict) else {}


def _save_state(db: Session, tg: int, st: dict) -> None:
    row = db.get(fm.FarmSetting, _skey(tg))
    if row is None:
        db.add(fm.FarmSetting(key=_skey(tg), value=st))
    else:
        row.value = st
    db.commit()


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_sleep(prefs: dict, now_msk: datetime) -> bool:
    s = prefs.get("sleep") or {}
    if not s.get("on"):
        return False
    a, b, t = _mins(s["from"]), _mins(s["to"]), now_msk.hour * 60 + now_msk.minute
    if a == b:
        return False
    return (a <= t < b) if a < b else (t >= a or t < b)


def _since_ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


def recipients(db: Session) -> list[int]:
    """Кому слать: владельцы/админы с Telegram + все, кто сохранил настройки."""
    from app.db import models  # noqa: PLC0415

    ids = set(get_settings().owner_telegram_ids or [])
    for u in db.scalars(select(models.User).where(models.User.is_admin.is_(True))):
        if u.telegram_user_id:
            ids.add(int(u.telegram_user_id))
    for row in db.scalars(select(fm.FarmSetting).where(fm.FarmSetting.key.like("notif:tg:%"))):
        try:
            ids.add(int(row.key.rsplit(":", 1)[1]))
        except ValueError:
            continue
    return sorted(ids)


def _places(db: Session) -> tuple[dict, dict]:
    conts = {c.id: c.name for c in db.scalars(select(fm.FarmContainer))}
    sites = {s.id: s.name for s in db.scalars(select(fm.FarmSite))}
    return conts, sites


def snapshot(db: Session, prefs: dict, now_ts: float) -> dict:
    """Что сейчас «держится» дольше hold_min: offline, ошибки, low hashrate."""
    hold = prefs["hold_min"] * 60
    lvl = prefs["min_level"]
    off, al, low = {}, {}, {}
    for d in db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.in_operation.is_(True))):
        if d.status == "offline":
            since = d.status_since.timestamp() if d.status_since else now_ts
            if now_ts - since >= hold:
                off[d.id] = d
            continue
        for a in d.alarms or []:
            if a.get("cls") == "offline":
                continue
            since = _since_ts(a.get("since")) or now_ts
            if now_ts - since < hold:
                continue
            if a.get("cls") == "hashrate":
                low[f"{d.id}:{a.get('code')}"] = (d, a)
            elif int(a.get("level") or 0) >= lvl:
                al[f"{d.id}:{a.get('code')}"] = (d, a)
    return {"off": off, "al": al, "low": low}


def _dev_label(d: fm.FarmDevice, conts: dict) -> str:
    place = conts.get(d.container_id) or "—"
    name = d.worker or d.ip or d.serial or f"#{d.id}"
    return f"{place} · {d.model or '?'} · {name}"


def _top_places(devs: list, conts: dict, n: int = 4) -> str:
    c = Counter(conts.get(d.container_id) or "без контейнера" for d in devs)
    parts = [f"{k}: {v}" for k, v in c.most_common(n)]
    if len(c) > n:
        parts.append(f"ещё {len(c) - n}")
    return ", ".join(parts)


def build_digest(db: Session, prefs: dict, st: dict, now_ts: float) -> tuple[str | None, dict, int]:
    """Текст дайджеста (или None), новое состояние, число новых offline."""
    snap = snapshot(db, prefs, now_ts)
    conts, _ = _places(db)
    t = prefs["types"]
    first = "off" not in st
    rep_off = set(st.get("off", []))
    rep_al = set(st.get("al", []))
    rep_low = set(st.get("low", []))
    cur_off = set(snap["off"])
    cur_al = set(snap["al"])
    cur_low = set(snap["low"])
    new_off = [snap["off"][i] for i in sorted(cur_off - rep_off)]
    back_ids = rep_off - cur_off
    back = [d for d in db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.id.in_(back_ids)))] if back_ids else []
    still = {d.id for d in back if d.status == "offline"}  # сообщены, ещё без связи (меньше hold) — держим
    back = [d for d in back if d.status != "offline"]
    new_al = [snap["al"][k] for k in sorted(cur_al - rep_al)]
    new_low = [snap["low"][k] for k in sorted(cur_low - rep_low)]
    cursor = int(st.get("ev", 0))
    pool_ev = []
    q = select(fm.FarmDeviceEvent).where(fm.FarmDeviceEvent.id > cursor, fm.FarmDeviceEvent.kind.in_(("pool", "worker"))).order_by(fm.FarmDeviceEvent.id)
    for e in db.scalars(q.limit(500)):
        pool_ev.append(e)
    last_ev = db.scalar(select(fm.FarmDeviceEvent.id).order_by(fm.FarmDeviceEvent.id.desc()).limit(1)) or cursor
    new_state = {"off": sorted(cur_off | still), "al": sorted(cur_al), "low": sorted(cur_low), "ev": int(last_ev),
                 "last": now_ts, "daily": st.get("daily")}
    if "pwr" in st:
        new_state["pwr"] = st["pwr"]
    if first:
        return None, new_state, 0
    # Вернулись — убираем из «сообщённых», даже если тип выключен.
    lines: list[str] = []
    maxl = prefs["max_lines"]
    now_msk = datetime.fromtimestamp(now_ts, MSK)
    if t["offline"] and new_off:
        lines.append(f"🔴 <b>Нет связи: +{len(new_off)}</b> ({_top_places(new_off, conts)})")
        for d in new_off[:maxl]:
            lines.append(f"   • {_dev_label(d, conts)}")
        if len(new_off) > maxl:
            lines.append(f"   … и ещё {len(new_off) - maxl}")
    if t["online"] and back:
        lines.append(f"🟢 <b>Вернулись в работу: {len(back)}</b> ({_top_places(back, conts)})")
    if t["errors"] and new_al:
        by_cls = Counter(CLASSES.get(a.get("cls"), (a.get("cls"), 0))[0] for _, a in new_al)
        lines.append("⚠️ <b>Новые ошибки: " + str(len(new_al)) + "</b> — " + ", ".join(f"{k} {v}" for k, v in by_cls.most_common()))
        for d, a in new_al[:maxl]:
            lines.append(f"   • {_dev_label(d, conts)} — {a.get('msg')}")
        if len(new_al) > maxl:
            lines.append(f"   … и ещё {len(new_al) - maxl}")
    if t["lowhash"] and new_low:
        lines.append(f"📉 <b>Low hashrate: +{len(new_low)}</b> ({_top_places([d for d, _ in new_low], conts)})")
    if t["pool"] and pool_ev:
        kinds = Counter("пул" if e.kind == "pool" else "воркер" for e in pool_ev)
        lines.append("🔄 <b>Смена " + ", ".join(f"{k}: {v}" for k, v in kinds.items()) + "</b>")
    if not lines:
        return None, new_state, len(new_off)
    head = f"🔔 <b>Kirka · ферма</b> · {now_msk:%H:%M}"
    return "\n".join([head, ""] + lines), new_state, len(new_off)


# ---------- «Вероятно, питание» ----------
POWER_SHARE = 0.9    # доля асиков контейнера без связи
POWER_BURST = 0.8    # доля, пропавшая в одном окне — «разом», а не постепенно
POWER_WINDOW = 15 * 60
POWER_HOLD = 3 * 60  # держится хотя бы 3 минуты — не мигание
POWER_MIN = 5        # маленькие группы не считаем
POWER_BACK = 0.5     # вернулось больше половины — «питание вернулось»


def grid_of(name: str) -> tuple[str | None, list[str]]:
    """Фидер и КТП из названия контейнера: «11к 21 Ф24 (КТП 7) …» → ("Ф24", ["7"])."""
    import re  # noqa: PLC0415

    f = re.search(r"Ф\s*(\d+)", name or "")
    ktp: list[str] = []
    for m in re.finditer(r"КТП\s*([\d,\s]+)", name or ""):
        ktp += [x for x in re.split(r"[,\s]+", m.group(1)) if x]
    return (f"Ф{f.group(1)}" if f else None), list(dict.fromkeys(ktp))


def _short(name: str) -> str:
    return (name or "").split(" ")[0] or name


def power_scan(db: Session, now_ts: float) -> dict[int, dict]:
    """Контейнеры, которые сейчас выглядят обесточенными (или потеряли связь целиком)."""
    by: dict[int, list] = {}
    for d in db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.in_operation.is_(True), fm.FarmDevice.container_id.is_not(None))):
        by.setdefault(d.container_id, []).append(d)
    conts = {c.id: c.name for c in db.scalars(select(fm.FarmContainer))}
    out = {}
    for cid, devs in by.items():
        total = len(devs)
        if total < POWER_MIN:
            continue
        since = sorted((d.status_since.timestamp() if d.status_since else now_ts) for d in devs if d.status == "offline")
        if len(since) < POWER_SHARE * total or now_ts - since[-1] < POWER_HOLD and now_ts - since[0] < POWER_HOLD:
            continue
        last = since[-1]
        burst = sum(1 for t in since if last - t <= POWER_WINDOW)
        name = conts.get(cid) or f"#{cid}"
        feeder, ktp = grid_of(name)
        out[cid] = {"name": name, "total": total, "off": len(since), "at": last, "burst": burst >= POWER_BURST * total,
                    "feeder": feeder, "ktp": ktp}
    return out


def _all_containers_grid(db: Session) -> dict[int, tuple[str | None, list[str]]]:
    ids = {cid for (cid,) in db.execute(select(fm.FarmDevice.container_id).where(fm.FarmDevice.in_operation.is_(True)).distinct()) if cid}
    return {c.id: grid_of(c.name) for c in db.scalars(select(fm.FarmContainer).where(fm.FarmContainer.id.in_(ids)))} if ids else {}


def power_check(db: Session, st: dict, now_ts: float) -> tuple[str | None, dict, list[int]]:
    """Новые «обесточенные» контейнеры и вернувшиеся. Возвращает (текст, новое состояние pwr, id асиков)."""
    scan = power_scan(db, now_ts)
    rep = {int(k): v for k, v in (st.get("pwr") or {}).items()}
    first = "pwr" not in st
    new = {cid: v for cid, v in scan.items() if cid not in rep and v["burst"]}
    # вернулись: о контейнере писали, а без связи теперь меньше половины
    back = {}
    if rep:
        cur = {}
        for d in db.scalars(select(fm.FarmDevice).where(fm.FarmDevice.in_operation.is_(True), fm.FarmDevice.container_id.in_(list(rep)))):
            c = cur.setdefault(d.container_id, [0, 0])
            c[0] += 1
            c[1] += d.status == "offline"
        for cid, info in rep.items():
            t, off = cur.get(cid, [0, 0])
            if not t or off < POWER_BACK * t:
                back[cid] = {"name": info.get("name") or f"#{cid}", "total": t, "on": t - off}
    keep = {cid: v for cid, v in rep.items() if cid not in back}
    keep.update({cid: {"name": v["name"], "at": v["at"]} for cid, v in (scan if first else new).items()})
    if first or (not new and not back):
        return None, {str(k): v for k, v in keep.items()}, []
    now_msk = datetime.fromtimestamp(now_ts, MSK)
    lines = [f"⚡ <b>Kirka · вероятно, питание</b> · {now_msk:%H:%M}", ""] if new else [f"⚡ <b>Kirka · питание</b> · {now_msk:%H:%M}", ""]
    dev_ids: list[int] = []
    if new:
        for cid, v in sorted(new.items(), key=lambda x: x[1]["name"]):
            grid = ", ".join(x for x in [v["feeder"], ("КТП " + ",".join(v["ktp"])) if v["ktp"] else None] if x)
            at = datetime.fromtimestamp(v["at"], MSK)
            lines.append(f"🔴 {_short(v['name'])}{f' ({grid})' if grid else ''}: пропали разом {v['off']} из {v['total']} в {at:%H:%M}")
        dev_ids = [d for (d,) in db.execute(select(fm.FarmDevice.id).where(fm.FarmDevice.container_id.in_(list(new)), fm.FarmDevice.status == "offline"))]
        # весь КТП / весь фидер
        grids = _all_containers_grid(db)
        down = set(keep)
        ktp_all: dict[str, set] = {}
        fdr_all: dict[str, set] = {}
        for cid, (f, ks) in grids.items():
            if len(ks) == 1:
                ktp_all.setdefault(ks[0], set()).add(cid)
            if f:
                fdr_all.setdefault(f, set()).add(cid)
        new_k = {k for v in new.values() if len(v["ktp"]) == 1 for k in v["ktp"]}
        new_f = {v["feeder"] for v in new.values() if v["feeder"]}
        whole_f = [f for f in sorted(new_f) if fdr_all.get(f) and len(fdr_all[f]) > 1 and fdr_all[f] <= down]
        whole_k = [k for k in sorted(new_k, key=lambda x: int(x)) if ktp_all.get(k) and len(ktp_all[k]) > 1 and ktp_all[k] <= down
                   and not any(ktp_all[k] <= fdr_all.get(f, set()) for f in whole_f)]
        lines.append("")
        for f in whole_f:
            lines.append(f"Без связи весь фидер {f} ({len(fdr_all[f])} конт.) — похоже на отключение со стороны сети.")
        for k in whole_k:
            lines.append(f"Без связи весь КТП {k} — проверьте ввод и автоматы КТП.")
        lines.append("Если питание есть — проверьте связь контейнера: коммутатор, провайдер.")
    if back:
        if new:
            lines.append("")
        for cid, v in sorted(back.items(), key=lambda x: x[1]["name"]):
            lines.append(f"🟢 {_short(v['name'])}: вернулось, в работе {v['on']} из {v['total']}")
        # вернувшиеся асики не повторяем в сводке «Вернулись в работу»
        dev_ids += [-d for (d,) in db.execute(select(fm.FarmDevice.id).where(fm.FarmDevice.container_id.in_(list(back)), fm.FarmDevice.status != "offline"))]
    return "\n".join(lines), {str(k): v for k, v in keep.items()}, dev_ids


def daily_text(db: Session, now_ts: float) -> str:
    from app.api.v1.farm import summary as farm_summary  # noqa: PLC0415

    s = farm_summary(db)
    now_msk = datetime.fromtimestamp(now_ts, MSK)
    ths = float(s.get("ths") or 0)
    hr = f"{ths / 1000:.2f} PH/s" if ths >= 1000 else f"{ths:.1f} TH/s"
    eff = f" ({s['efficiency']}% от заводского)" if s.get("efficiency") is not None else ""
    top = sorted((c for c in s.get("containers", []) if c.get("offline")), key=lambda c: -c["offline"])[:3]
    lines = [f"☀️ <b>Kirka · сводка на {now_msk:%d.%m %H:%M}</b>", "",
             f"🟢 В работе: {s.get('working', 0)} из {s.get('total', 0)}",
             f"🔴 Нет связи: {s.get('offline', 0)}",
             f"😴 Сон: {s.get('suspended', 0)}",
             f"⚠️ С ошибками: {s.get('with_alarms', 0)}",
             f"⚡ Хешрейт: {hr}{eff} · {s.get('kw', 0)} кВт"]
    if top:
        lines.append("Больше всего без связи: " + ", ".join(f"{c['name']} {c['offline']}" for c in top))
    return "\n".join(lines)


def send_telegram(chat_id: int, text: str, *, buttons: bool = True) -> bool:
    token = get_settings().bot_token
    if not token:
        log.warning("BOT_TOKEN не задан — уведомление не отправлено")
        return False
    payload: dict = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[
            {"text": "🌐 Открыть ферму", "url": "https://kirkalab.ru/#map"},
            {"text": "⚙️ Настроить", "callback_data": "notif:open"},
        ]]}
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
        if r.status_code != 200:
            log.warning("telegram %s: %s", r.status_code, r.text[:200])
        return r.status_code == 200
    except httpx.HTTPError as exc:
        log.warning("telegram send failed: %s", exc)
        return False


def tick(db: Session, now_ts: float | None = None, sender=send_telegram) -> int:
    """Один проход по получателям. Возвращает число отправленных сообщений."""
    import time as _t  # noqa: PLC0415

    now_ts = now_ts or _t.time()
    now_msk = datetime.fromtimestamp(now_ts, MSK)
    sent = 0
    for tg in recipients(db):
        prefs = get_prefs(db, tg)
        if not prefs["enabled"]:
            continue
        st = _state(db, tg)
        sleeping = in_sleep(prefs, now_msk)
        # Утренняя сводка — один раз в сутки, не во сне.
        today = now_msk.date().isoformat()
        if prefs["types"]["daily"] and not sleeping and st.get("daily") != today and now_msk.hour * 60 + now_msk.minute >= _mins(prefs["daily_at"]):
            if "off" in st and sender(tg, daily_text(db, now_ts)):
                sent += 1
                _log_sent(db, tg, "daily", "Утренняя сводка")
            st["daily"] = today
            _save_state(db, tg, st)
        # «Вероятно, питание» — сразу, не дожидаясь сводки.
        if prefs["types"].get("power", True):
            ptext, pwr, dev_ids = power_check(db, st, now_ts)
            can = not sleeping or bool(prefs["wake_offline"])
            if ptext and can:
                if sleeping:
                    ptext += "\n\n<i>Сообщение пришло во время сна: пропал контейнер целиком.</i>"
                if sender(tg, ptext):
                    sent += 1
                    _log_sent(db, tg, "power", ptext)
                    # асики этих контейнеров уже «сообщены» — сводка не повторит их списком
                    add = {i for i in dev_ids if i > 0}
                    drop = {-i for i in dev_ids if i < 0}
                    st["off"] = sorted((set(st.get("off", [])) | add) - drop)
                    st["pwr"] = pwr
                    _save_state(db, tg, st)
            elif not ptext and st.get("pwr") != pwr:
                st["pwr"] = pwr
                _save_state(db, tg, st)
        due = now_ts - float(st.get("last") or 0) >= prefs["every_min"] * 60
        if not due and "off" in st:
            continue
        text, new_state, n_off = build_digest(db, prefs, st, now_ts)
        if sleeping:
            wake = prefs["wake_offline"] and n_off >= prefs["wake_offline"] and prefs["types"]["offline"]
            if not wake:
                if "off" not in st:
                    _save_state(db, tg, new_state)
                continue
            text = (text or "") + "\n\n<i>Сообщение пришло во время сна: массовое отключение.</i>"
        if text and sender(tg, text):
            sent += 1
            _log_sent(db, tg, "digest", text)
        _save_state(db, tg, new_state)
    return sent


def send_test(db: Session, tg: int) -> bool:
    prefs = get_prefs(db, tg)
    on = [TYPES[k] for k, v in prefs["types"].items() if v]
    s = prefs["sleep"]
    text = ("🔔 <b>Kirka · проверка уведомлений</b>\n\n"
            f"Получаете: {', '.join(on) or 'ничего'}\n"
            f"Ошибки: {LEVEL_NAMES[prefs['min_level']]}\n"
            f"Держится не меньше {prefs['hold_min']} мин, сводка раз в {prefs['every_min']} мин\n"
            f"Сон: {'с ' + s['from'] + ' до ' + s['to'] if s['on'] else 'выключен'}")
    ok = send_telegram(tg, text)
    if ok:
        _log_sent(db, tg, "test", "Проверка уведомлений")
    return ok


def _log_sent(db: Session, tg: int, kind: str, text: str) -> None:
    """Пишем отправленное в журнал: лента «Что приходило» на сайте и админка."""
    import re  # noqa: PLC0415

    from app.services import activity as act  # noqa: PLC0415

    plain = re.sub(r"<[^>]+>", "", text or "").strip()
    act.record(db, source="notify", action=kind, tg_id=tg, label=plain[:300], meta={"text": plain[:1500]})
