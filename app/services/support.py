"""Переписка через бота, рассылки выбранным, менеджеры, выдача полного доступа.

В текстах, которые бот шлёт людям, нет слов «купить», «оплатить», «тариф», «цена», «скидка».
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db.activity_models import SupportMessage
from app.db.farm_models import FarmSetting
from app.services import activity as act

log = logging.getLogger(__name__)
MANAGERS_KEY = "support:managers"
MSK = timezone(timedelta(hours=3))


# ---------- Telegram ----------
def tg_send(chat_id: int, text: str, *, reply_button: bool = True) -> int | None:
    """Отправляет сообщение от бота; возвращает message_id или None."""
    token = get_settings().bot_token
    if not token:
        return None
    payload: dict = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if reply_button:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": "✉️ Ответить", "callback_data": "sup:write"}]]}
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
        if r.status_code == 200:
            return (r.json().get("result") or {}).get("message_id")
        log.warning("support send %s: %s", r.status_code, r.text[:200])
    except httpx.HTTPError as exc:
        log.warning("support send failed: %s", exc)
    return None


# ---------- менеджеры ----------
def managers(db: Session) -> list[int]:
    row = db.get(FarmSetting, MANAGERS_KEY)
    ids = [int(x) for x in (row.value or [])] if row and isinstance(row.value, list) else []
    if not ids:
        ids = [u for (u,) in db.execute(select(models.User.telegram_user_id).where(models.User.is_admin.is_(True), models.User.telegram_user_id.is_not(None)))]
    return ids


def set_managers(db: Session, ids: list[int]) -> list[int]:
    ids = sorted({int(x) for x in ids if int(x) > 0})
    row = db.get(FarmSetting, MANAGERS_KEY)
    if row:
        row.value = ids
    else:
        db.add(FarmSetting(key=MANAGERS_KEY, value=ids))
    db.commit()
    return managers(db)


def _who(meta: dict | None, tg: int) -> str:
    m = meta or {}
    name = m.get("name") or ""
    un = f"@{m['username']}" if m.get("username") else ""
    return (" ".join(x for x in (name, un) if x) or str(tg))


# ---------- входящие ----------
def incoming(db: Session, tg: int, text: str, meta: dict | None = None, sender=None) -> SupportMessage:
    """Человек написал менеджеру: сохраняем и пересылаем менеджерам (ответ — reply на пересланное)."""
    sender = sender or tg_send
    msg = SupportMessage(tg_id=tg, direction="in", text=text[:4000], status="new", meta=dict(meta or {}))
    db.add(msg)
    db.commit()
    fwd = {}
    for m in managers(db):
        if m == tg:
            continue
        mid = sender(m, f"✉️ Сообщение от {_who(meta, tg)} (ID {tg}):\n\n{text[:3500]}\n\n— Ответьте на это сообщение (свайп/«Ответить»), и бот передаст ответ. Или напишите из админки на сайте.", reply_button=False)
        if mid:
            fwd[str(m)] = mid
    msg.meta = dict(msg.meta or {}, fwd=fwd)
    db.commit()
    act.record(db, source="bot", action="support:in", tg_id=tg, label="Написал менеджеру")
    return msg


def find_by_forward(db: Session, manager_tg: int, reply_to_message_id: int) -> SupportMessage | None:
    rows = db.scalars(select(SupportMessage).where(SupportMessage.direction == "in").order_by(SupportMessage.id.desc()).limit(500))
    for r in rows:
        if (r.meta or {}).get("fwd", {}).get(str(manager_tg)) == reply_to_message_id:
            return r
    return None


# ---------- исходящие ----------
def send_out(db: Session, tg_ids: list[int], text: str, *, by_user_id: int | None = None, sender=None) -> dict:
    sender = sender or tg_send
    text = (text or "").strip()
    if not text:
        raise ValueError("Пустое сообщение")
    ids = list(dict.fromkeys(int(x) for x in tg_ids))
    if not ids:
        raise ValueError("Не выбраны получатели")
    bid = str(uuid.uuid4()) if len(ids) > 1 else None
    sent = failed = 0
    for tg in ids:
        mid = sender(tg, text)
        ok = bool(mid)
        sent += ok
        failed += not ok
        db.add(SupportMessage(tg_id=tg, direction="out", text=text[:4000], by_user_id=by_user_id,
                              status="sent" if ok else "failed", broadcast=bid, meta={"message_id": mid} if mid else None))
    # Ответили — входящие этого человека считаем прочитанными.
    for m in db.scalars(select(SupportMessage).where(SupportMessage.tg_id.in_(ids), SupportMessage.direction == "in", SupportMessage.status == "new")):
        m.status = "read"
    db.commit()
    for tg in ids:
        act.record(db, source="notify", action="support:out", tg_id=tg, label=("Рассылка: " if bid else "Сообщение от менеджера: ") + text[:200], meta={"text": text[:1500]})
    return {"sent": sent, "failed": failed, "broadcast": bid}


def thread(db: Session, tg: int, limit: int = 200) -> list[dict]:
    rows = db.scalars(select(SupportMessage).where(SupportMessage.tg_id == tg).order_by(SupportMessage.ts.desc(), SupportMessage.id.desc()).limit(limit)).all()
    return [{"id": r.id, "ts": act._iso(r.ts), "dir": r.direction, "text": r.text, "status": r.status, "broadcast": bool(r.broadcast)} for r in reversed(rows)]


def mark_read(db: Session, tg: int) -> int:
    n = 0
    for m in db.scalars(select(SupportMessage).where(SupportMessage.tg_id == tg, SupportMessage.direction == "in", SupportMessage.status == "new")):
        m.status = "read"
        n += 1
    db.commit()
    return n


def unread(db: Session) -> dict[int, int]:
    return dict(db.execute(select(SupportMessage.tg_id, func.count()).where(SupportMessage.direction == "in", SupportMessage.status == "new").group_by(SupportMessage.tg_id)).all())


def last_in(db: Session) -> dict[int, dict]:
    out = {}
    for r in db.scalars(select(SupportMessage).order_by(SupportMessage.id.desc()).limit(2000)):
        if r.tg_id not in out:
            out[r.tg_id] = {"ts": act._iso(r.ts), "dir": r.direction, "text": r.text[:120]}
    return out


# ---------- доступ ----------
KIND_NAMES = {"gift": "подарок", "trial": "пробный", "paid": "оплачен", "manual": "вручную"}


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _kind(charge_id: str) -> str:
    p = (charge_id or "").split(":", 1)[0]
    return p if p in ("gift", "trial", "manual") else "paid"


def access_info(db: Session, user: models.User | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if not user:
        return {"state": "none", "label": "нет аккаунта", "until": None, "days_left": None, "kind": None, "trial_used": False, "history": []}
    subs = db.scalars(select(models.Subscription).where(models.Subscription.user_id == user.id).order_by(models.Subscription.id.desc())).all()
    until = _aware(user.premium_until)
    on = bool(user.is_pro and (until is None or until > now))
    cur = next((s for s in subs if s.status == "active" and (_aware(s.expires_at) or now) > now), None)
    kind = _kind(cur.telegram_payment_charge_id) if cur else ("manual" if on else None)
    days = max(0, -(-int((until - now).total_seconds()) // 86400)) if on and until else None
    hist = [{"id": s.id, "kind": _kind(s.telegram_payment_charge_id), "status": s.status, "from": act._iso(s.started_at),
             "to": act._iso(s.expires_at)} for s in subs[:20]]
    if on:
        label = "PRO" + (f" · {KIND_NAMES.get(kind, kind)}" if kind else "") + (f" · до {until.astimezone(MSK):%d.%m.%Y}" if until else " · бессрочно")
    else:
        label = "FREE"
    return {"state": "pro" if on else "free", "label": label, "until": act._iso(until) if on else None, "days_left": days,
            "kind": kind, "trial_used": any(h["kind"] == "trial" for h in hist), "history": hist}


def grant(db: Session, user: models.User, days: int, kind: str = "gift", now: datetime | None = None) -> models.Subscription:
    """Открыть/продлить полный доступ на N дней (складывается с остатком)."""
    if kind not in ("gift", "trial", "manual"):
        raise ValueError("kind")
    days = int(days)
    if not 1 <= days <= 3650:
        raise ValueError("Срок от 1 до 3650 дней")
    now = now or datetime.now(UTC)
    until = _aware(user.premium_until)
    base = until if (user.is_pro and until and until > now) else now
    exp = base + timedelta(days=days)
    sub = models.Subscription(user_id=user.id, plan_code="pro_monthly", status="active", started_at=now, expires_at=exp,
                              telegram_payment_charge_id=f"{kind}:{uuid.uuid4()}", total_amount=0)
    db.add(sub)
    user.is_pro = True
    user.premium_until = exp
    db.commit()
    db.refresh(sub)
    return sub


def revoke(db: Session, user: models.User, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    for s in db.scalars(select(models.Subscription).where(models.Subscription.user_id == user.id, models.Subscription.status == "active")):
        s.status = "revoked"
        s.expires_at = now
    user.is_pro = False
    user.premium_until = now
    db.commit()


def access_text(kind: str, days: int, until: datetime) -> str:
    d = f"{until.astimezone(MSK):%d.%m.%Y}"
    if kind == "trial":
        return f"🎁 Вам открыт пробный период Kirka: полный доступ на {days} дн., до {d}.\n\nВсе разделы бота и сайта доступны без ограничений. Вопросы — кнопка «Ответить» ниже."
    return f"🎁 Вам открыт полный доступ Kirka на {days} дн., до {d}.\n\nВсе разделы бота и сайта доступны без ограничений. Вопросы — кнопка «Ответить» ниже."
