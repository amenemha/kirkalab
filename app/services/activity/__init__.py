"""Журнал действий и сводка для админки."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db.activity_models import ActivityEvent

log = logging.getLogger(__name__)

# Подписи кнопок/команд бота — самое длинное совпадение префикса.
BOT_LABELS = {
    "/start": "Запустил бота (/start)", "/menu": "Меню", "/ferma": "Ферма", "/problems": "Проблемы",
    "/pools": "Пулы", "/notify": "Уведомления", "/site": "Ссылка на сайт", "/help": "Помощь",
    "/me": "Профиль", "/cancel": "Отмена", "/logout": "Выход", "/health": "Проверка связи",
    "hub:farm": "Ферма", "hub:problems": "Проблемы", "hub:menu": "Меню", "hub:help": "Помощь",
    "hub:": "Меню", "menu:calculator": "Калькулятор", "menu:catalog": "Каталог ASIC",
    "menu:reports": "Мои отчёты", "menu:profile": "Профиль", "menu:help": "Помощь", "menu:": "Меню",
    "calc:xlsx": "Калькулятор · выгрузка Excel", "calc:compare": "Калькулятор · сравнение",
    "calc:qty": "Калькулятор · количество", "calc:price": "Калькулятор · цена электроэнергии",
    "calc:m:": "Калькулятор · выбрал модель", "calc:b:": "Калькулятор · выбрал бренд",
    "calc:manual": "Калькулятор · ручной ввод", "calc:": "Калькулятор",
    "cat:m:": "Каталог ASIC · модель", "cat:fw:": "Каталог ASIC · прошивка", "cat:": "Каталог ASIC",
    "hist:open": "Мои отчёты · открыл отчёт", "hist:xlsx": "Мои отчёты · Excel", "hist:": "Мои отчёты",
    "notif:test": "Уведомления · проверка", "notif:": "Уведомления · настройка",
    "pools:": "Пулы", "farm:": "Ферма", "profile:": "Профиль",
    "qr:approve": "Вход на сайт по QR — подтвердил", "qr:reject": "Вход на сайт по QR — отклонил",
    "plan:": "PRO", "text": "Ввёл текст",
}
SITE_LABELS = {
    "login": "Вошёл на сайт", "today": "Обзор", "map": "Контейнеры", "cont": "Карта контейнера",
    "asics": "Асики", "errors": "Проблемы", "clients": "Клиенты", "pools": "Пулы",
    "calc": "Калькулятор", "notify": "Уведомления", "energy": "Энергетика", "admin": "Админка",
}
# Что считаем «смотрел ферму/асики».
FARM_WORDS = ("Ферма", "Проблемы", "Пулы", "Обзор", "Контейнеры", "Карта контейнера", "Асики", "Энергетика", "Клиенты")


def bot_label(action: str) -> str:
    a = (action or "").strip()
    if a.startswith("/"):
        a = a.split()[0].split("@")[0]
    best = ""
    for k in BOT_LABELS:
        if a.startswith(k) and len(k) > len(best):
            best = k
    return BOT_LABELS.get(best, a[:60] or "—")


def site_label(page: str, arg: str | None = None) -> str:
    base = SITE_LABELS.get(page, page)
    return f"{base} · {arg}" if arg else base


def record(db: Session, *, source: str, action: str, tg_id: int | None = None, user_id: int | None = None,
           label: str | None = None, meta: dict | None = None) -> None:
    """Пишет событие; журнал не должен ломать основной запрос."""
    try:
        if tg_id and not user_id:
            user_id = db.scalar(select(models.User.id).where(models.User.telegram_user_id == tg_id))
        if user_id and not tg_id:
            tg_id = db.scalar(select(models.User.telegram_user_id).where(models.User.id == user_id))
        db.add(ActivityEvent(source=source, action=action[:120], tg_id=tg_id, user_id=user_id,
                             label=(label or "")[:300] or None, meta=meta or None))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("activity log failed: %s", exc)


def _tg_name(db: Session, tg: int, cache: dict) -> dict:
    """Имя и @username из Telegram (getChat), кешируется в farm_settings."""
    from app.db.farm_models import FarmSetting  # noqa: PLC0415

    key = f"tgname:{tg}"
    if key in cache:
        return cache[key]
    row = db.get(FarmSetting, key)
    if row and isinstance(row.value, dict) and row.value.get("at") and (datetime.now(UTC).timestamp() - row.value["at"] < 86400):
        cache[key] = row.value
        return row.value
    val = {"username": None, "name": None, "at": datetime.now(UTC).timestamp()}
    token = get_settings().bot_token
    if token:
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getChat", params={"chat_id": tg}, timeout=6)
            j = r.json().get("result") or {}
            val["username"] = j.get("username")
            val["name"] = " ".join(x for x in (j.get("first_name"), j.get("last_name")) if x) or None
        except Exception:  # noqa: BLE001
            pass
    try:
        if row:
            row.value = val
        else:
            db.add(FarmSetting(key=key, value=val))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    cache[key] = val
    return val


def _iso(d):
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.isoformat()


def users_summary(db: Session) -> list[dict]:
    users = {u.telegram_user_id: u for u in db.scalars(select(models.User)) if u.telegram_user_id}
    agg = {r.tg_id: r for r in db.execute(
        select(ActivityEvent.tg_id, func.min(ActivityEvent.ts).label("first"), func.max(ActivityEvent.ts).label("last"),
               func.count().filter(ActivityEvent.source == "bot").label("bot"),
               func.count().filter(ActivityEvent.source == "site").label("site"),
               func.count().filter(ActivityEvent.source == "notify").label("notif"))
        .where(ActivityEvent.tg_id.is_not(None)).group_by(ActivityEvent.tg_id))}
    calcs = dict(db.execute(select(models.CalculationRun.user_id, func.count()).group_by(models.CalculationRun.user_id)).all())
    qr = {r[0]: (r[1], r[2]) for r in db.execute(
        select(models.QrLoginSession.telegram_user_id, func.count(), func.max(models.QrLoginSession.created_at))
        .where(models.QrLoginSession.status.in_(("approved", "consumed"))).group_by(models.QrLoginSession.telegram_user_id))}
    cache: dict = {}
    out = []
    for tg in sorted(set(users) | set(agg)):
        u, a = users.get(tg), agg.get(tg)
        last = db.scalars(select(ActivityEvent).where(ActivityEvent.tg_id == tg, ActivityEvent.source != "notify")
                          .order_by(ActivityEvent.ts.desc()).limit(6)).all()
        labels = {x for (x,) in db.execute(select(ActivityEvent.label).where(ActivityEvent.tg_id == tg).distinct())}
        nm = _tg_name(db, tg, cache)
        first = min([d for d in (u.created_at if u else None, a.first if a else None) if d], default=None)
        lastt = max([d for d in (a.last if a else None, qr.get(tg, (0, None))[1]) if d], default=None)
        out.append({
            "tg_id": tg, "user_id": u.id if u else None, "username": nm.get("username"), "name": nm.get("name"),
            "is_admin": bool(u and u.is_admin), "first_seen": _iso(first), "last_seen": _iso(lastt or first),
            "bot_actions": a.bot if a else 0, "site_views": a.site if a else 0, "notifications": a.notif if a else 0,
            "calcs": calcs.get(u.id, 0) if u else 0, "site_logins": qr.get(tg, (0, None))[0],
            "started": any((lb or "").startswith("Запустил бота") for lb in labels) or bool(u),
            "saw_farm": any(any(w in (lb or "") for w in FARM_WORDS) for lb in labels),
            "last": [{"ts": _iso(e.ts), "source": e.source, "label": e.label or e.action} for e in last],
        })
    out.sort(key=lambda r: r["last_seen"] or "", reverse=True)
    return out


def feed(db: Session, tg: int | None = None, source: str | None = None, limit: int = 200) -> list[dict]:
    q = select(ActivityEvent).order_by(ActivityEvent.ts.desc(), ActivityEvent.id.desc()).limit(max(1, min(limit, 1000)))
    if tg:
        q = q.where(ActivityEvent.tg_id == tg)
    if source:
        q = q.where(ActivityEvent.source == source)
    return [{"ts": _iso(e.ts), "tg_id": e.tg_id, "source": e.source, "action": e.action,
             "label": e.label or e.action, "text": (e.meta or {}).get("text")} for e in db.scalars(q)]
