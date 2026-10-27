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


MEDIA_METHOD = {"photo": "sendPhoto", "document": "sendDocument", "video": "sendVideo", "voice": "sendVoice", "audio": "sendAudio"}
MAX_FILE = 20 * 1024 * 1024  # Telegram Bot API отдаёт боту файлы до 20 МБ


def _file_from_result(res: dict) -> dict | None:
    if res.get("photo"):
        p = res["photo"][-1]
        return {"type": "photo", "file_id": p["file_id"], "size": p.get("file_size")}
    for k in ("document", "video", "voice", "audio"):
        if res.get(k):
            d = res[k]
            return {"type": k, "file_id": d["file_id"], "name": d.get("file_name"), "size": d.get("file_size"), "mime": d.get("mime_type")}
    return None


def tg_media(chat_id: int, file: dict, caption: str = "", *, reply_button: bool = True) -> tuple[int | None, dict | None]:
    """Отправляет файл от бота. file: {type, file_id} или {type, data: bytes, name, mime}.

    Возвращает (message_id, file) — file с file_id Telegram, чтобы не загружать повторно.
    """
    token = get_settings().bot_token
    if not token:
        return None, None
    kind = file.get("type") if file.get("type") in MEDIA_METHOD else "document"
    payload: dict = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption[:1024]
    if reply_button:
        import json as _json
        payload["reply_markup"] = _json.dumps({"inline_keyboard": [[{"text": "✉️ Ответить", "callback_data": "sup:write"}]]})
    url = f"https://api.telegram.org/bot{token}/{MEDIA_METHOD[kind]}"
    try:
        if file.get("data") is not None:
            files = {kind: (file.get("name") or "file", file["data"], file.get("mime") or "application/octet-stream")}
            r = httpx.post(url, data=payload, files=files, timeout=120)
        else:
            payload[kind] = file["file_id"]
            r = httpx.post(url, data=payload, timeout=30)
        if r.status_code == 200:
            res = r.json().get("result") or {}
            f = _file_from_result(res) or {}
            if file.get("name") and not f.get("name"):
                f["name"] = file["name"]
            return res.get("message_id"), f or None
        log.warning("support media %s: %s", r.status_code, r.text[:200])
    except httpx.HTTPError as exc:
        log.warning("support media failed: %s", exc)
    return None, None


def tg_download(file_id: str) -> tuple[bytes, str | None] | None:
    token = get_settings().bot_token
    if not token:
        return None
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}, timeout=20)
        path = (r.json().get("result") or {}).get("file_path") if r.status_code == 200 else None
        if not path:
            return None
        d = httpx.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=120)
        return (d.content, path.rsplit("/", 1)[-1]) if d.status_code == 200 else None
    except httpx.HTTPError as exc:
        log.warning("support download failed: %s", exc)
        return None


def _deliver(chat_id: int, text: str, file: dict | None, sender, media_sender, *, reply_button: bool = True) -> tuple[int | None, dict | None]:
    """Текст и/или файл одному человеку. Длинный текст — отдельным сообщением перед файлом."""
    if not file:
        return sender(chat_id, text, reply_button=reply_button), None
    mid = None
    cap = text
    if len(text) > 1000:
        mid = sender(chat_id, text, reply_button=False)
        cap = ""
    fmid, f = media_sender(chat_id, file, cap, reply_button=reply_button)
    return (fmid or mid), f


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
def incoming(db: Session, tg: int, text: str, meta: dict | None = None, sender=None, file: dict | None = None, media_sender=None) -> SupportMessage:
    """Человек написал менеджеру (текст и/или файл): сохраняем и пересылаем менеджерам (ответ — reply на пересланное)."""
    sender = sender or tg_send
    media_sender = media_sender or tg_media
    text = (text or "").strip()
    meta = dict(meta or {})
    if file:
        meta["file"] = {k: file[k] for k in ("type", "file_id", "name", "size", "mime") if file.get(k)}
    msg = SupportMessage(tg_id=tg, direction="in", text=text[:4000], status="new", meta=meta)
    db.add(msg)
    db.commit()
    fwd = {}
    for m in managers(db):
        if m == tg:
            continue
        if file:
            body = f"✉️ {('Файл' if file.get('type') != 'photo' else 'Фото')} от {_who(meta, tg)} (ID {tg})" + (f":\n\n{text[:700]}" if text else "") + "\n\n— «Ответить» на это сообщение — ответ уйдёт человеку."
            mid, _ = media_sender(m, {"type": file.get("type"), "file_id": file["file_id"]}, body, reply_button=False)
        else:
            mid = sender(m, f"✉️ Сообщение от {_who(meta, tg)} (ID {tg}):\n\n{text[:3500]}\n\n— Ответьте на это сообщение (свайп/«Ответить»), и бот передаст ответ. Или напишите из админки на сайте.", reply_button=False)
        if mid:
            fwd[str(m)] = mid
    msg.meta = dict(msg.meta or {}, fwd=fwd)
    db.commit()
    act.record(db, source="bot", action="support:in", tg_id=tg, label="Написал менеджеру" + (" (файл)" if file else ""))
    return msg


def find_by_forward(db: Session, manager_tg: int, reply_to_message_id: int) -> SupportMessage | None:
    rows = db.scalars(select(SupportMessage).where(SupportMessage.direction == "in").order_by(SupportMessage.id.desc()).limit(500))
    for r in rows:
        if (r.meta or {}).get("fwd", {}).get(str(manager_tg)) == reply_to_message_id:
            return r
    return None


# ---------- исходящие ----------
def send_out(db: Session, tg_ids: list[int], text: str, *, by_user_id: int | None = None, sender=None,
             file: dict | None = None, media_sender=None) -> dict:
    """Сообщение/файл выбранным. file: {type, data, name, mime} (загрузка) или {type, file_id}."""
    sender = sender or tg_send
    media_sender = media_sender or tg_media
    text = (text or "").strip()
    if not text and not file:
        raise ValueError("Пустое сообщение")
    if file and file.get("data") is not None and len(file["data"]) > MAX_FILE:
        raise ValueError("Файл больше 20 МБ")
    ids = list(dict.fromkeys(int(x) for x in tg_ids))
    if not ids:
        raise ValueError("Не выбраны получатели")
    bid = str(uuid.uuid4()) if len(ids) > 1 else None
    sent = failed = 0
    cur = file
    for tg in ids:
        mid, f = _deliver(tg, text, cur, sender, media_sender)
        if f and f.get("file_id") and cur and cur.get("data") is not None:
            cur = dict(f, type=cur.get("type") if cur.get("type") in MEDIA_METHOD else f.get("type"))  # дальше — без повторной загрузки
        ok = bool(mid)
        sent += ok
        failed += not ok
        meta: dict = {"message_id": mid} if mid else {}
        fm = f or (cur if cur and cur.get("file_id") else None)
        if file:
            meta["file"] = {k: v for k, v in (fm or {"type": file.get("type"), "name": file.get("name")}).items() if k in ("type", "file_id", "name", "size", "mime") and v}
            meta["file"].setdefault("name", file.get("name"))
        db.add(SupportMessage(tg_id=tg, direction="out", text=text[:4000], by_user_id=by_user_id,
                              status="sent" if ok else "failed", broadcast=bid, meta=meta or None))
    # Ответили — входящие этого человека считаем прочитанными.
    for m in db.scalars(select(SupportMessage).where(SupportMessage.tg_id.in_(ids), SupportMessage.direction == "in", SupportMessage.status == "new")):
        m.status = "read"
    db.commit()
    for tg in ids:
        act.record(db, source="notify", action="support:out", tg_id=tg, label=("Рассылка: " if bid else "Сообщение от менеджера: ") + (text[:200] or "") + (" 📎 " + (file.get("name") or "файл") if file else ""), meta={"text": text[:1500]})
    return {"sent": sent, "failed": failed, "broadcast": bid}


def thread(db: Session, tg: int, limit: int = 200) -> list[dict]:
    rows = db.scalars(select(SupportMessage).where(SupportMessage.tg_id == tg).order_by(SupportMessage.ts.desc(), SupportMessage.id.desc()).limit(limit)).all()
    return [{"id": r.id, "ts": act._iso(r.ts), "dir": r.direction, "text": r.text, "status": r.status, "broadcast": bool(r.broadcast),
             "file": {k: v for k, v in ((r.meta or {}).get("file") or {}).items() if k in ("type", "name", "size", "mime")} or None} for r in reversed(rows)]


def file_of(db: Session, msg_id: int) -> dict | None:
    m = db.get(SupportMessage, msg_id)
    return ((m.meta or {}).get("file") if m else None) or None


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
            f = (r.meta or {}).get("file")
            out[r.tg_id] = {"ts": act._iso(r.ts), "dir": r.direction, "text": r.text[:120] or ("📎 " + (f.get("name") or ("фото" if f.get("type") == "photo" else "файл")) if f else "")}
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
