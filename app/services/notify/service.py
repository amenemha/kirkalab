"""Уведомления фермы в Telegram: сгруппированный дайджест изменений.

Не шлём каждое событие CROFT (их ~13 тыс. в сутки из-за «мигания»). Раз в
``every_min`` минут сравниваем текущее состояние с тем, о чём уже сообщили:

* «Нет связи»   — асик offline дольше ``hold_min`` минут и о нём ещё не писали;
* «Вернулись»   — о нём писали, а теперь он снова в работе;
* «Ошибки»      — активная ошибка уровня ≥ ``min_level`` держится ≥ ``hold_min``;
* «Low hashrate»— то же для класса hashrate;
* «Пул/воркер»  — события смены пула или воркера с прошлого дайджеста;
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
    "offline": "Нет связи",
    "online": "Вернулись в работу",
    "errors": "Новые ошибки",
    "lowhash": "Low hashrate",
    "pool": "Смена пула / воркера",
    "daily": "Утренняя сводка",
}
DEFAULTS: dict = {
    "enabled": True,
    "types": {"offline": True, "online": True, "errors": True, "lowhash": True, "pool": False, "daily": True},
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
    back = [d for d in back if d.status != "offline"]
    new_al = [snap["al"][k] for k in sorted(cur_al - rep_al)]
    new_low = [snap["low"][k] for k in sorted(cur_low - rep_low)]
    cursor = int(st.get("ev", 0))
    pool_ev = []
    q = select(fm.FarmDeviceEvent).where(fm.FarmDeviceEvent.id > cursor, fm.FarmDeviceEvent.kind.in_(("pool", "worker"))).order_by(fm.FarmDeviceEvent.id)
    for e in db.scalars(q.limit(500)):
        pool_ev.append(e)
    last_ev = db.scalar(select(fm.FarmDeviceEvent.id).order_by(fm.FarmDeviceEvent.id.desc()).limit(1)) or cursor
    new_state = {"off": sorted(cur_off), "al": sorted(cur_al), "low": sorted(cur_low), "ev": int(last_ev),
                 "last": now_ts, "daily": st.get("daily")}
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
