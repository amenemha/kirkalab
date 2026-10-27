"""Админка: кто пользуется ботом и сайтом, что смотрели; журнал страниц сайта."""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_admin, get_current_user
from app.db.session import get_db
from app.core.config import get_settings
from app.db import models
from app.services import activity as act
from app.services import support as sup

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])
track_router = APIRouter(tags=["activity"])


@router.get("/users")
def admin_users(db: Session = Depends(get_db)):
    rows = act.users_summary(db)
    unread, last, mgr = sup.unread(db), sup.last_in(db), set(sup.managers(db))
    for r in rows:
        u = db.get(models.User, r["user_id"]) if r["user_id"] else None
        r["access"] = sup.access_info(db, u)
        r["unread"] = unread.get(r["tg_id"], 0)
        r["last_msg"] = last.get(r["tg_id"])
        r["manager"] = r["tg_id"] in mgr
    rows.sort(key=lambda r: (-r["unread"], -(1 if r["last_msg"] and r["last_msg"]["dir"] == "in" else 0)))
    return {"users": rows, "total": len(rows), "started": sum(r["started"] for r in rows),
            "saw_farm": sum(r["saw_farm"] for r in rows), "on_site": sum(1 for r in rows if r["site_logins"] or r["site_views"]),
            "pro": sum(1 for r in rows if r["access"]["state"] == "pro"), "unread": sum(unread.values()),
            "test_mode": bool(get_settings().test_mode_all_pro), "managers": sorted(mgr)}


@router.get("/messages")
def admin_messages(tg: int, db: Session = Depends(get_db)):
    return {"items": sup.thread(db, tg)}


class OutMsg(BaseModel):
    tg_ids: list[int] = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=4000)


@router.post("/messages")
def admin_send(body: OutMsg, db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    try:
        return sup.send_out(db, body.tg_ids, body.text, by_user_id=admin.id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/messages/upload")
async def admin_send_file(
    tg_ids: str = Form(...),
    text: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Сообщение с файлом (скриншот, документ, видео) выбранным людям."""
    ids = [int(x) for x in tg_ids.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]
    if not ids or len(ids) > 500:
        raise HTTPException(422, "Не выбраны получатели")
    data = await file.read(sup.MAX_FILE + 1)
    if len(data) > sup.MAX_FILE:
        raise HTTPException(413, "Файл больше 20 МБ")
    mime = file.content_type or "application/octet-stream"
    kind = "photo" if mime in ("image/jpeg", "image/png", "image/webp") and len(data) <= 10 * 1024 * 1024 else \
        "video" if mime == "video/mp4" else "document"
    f = {"type": kind, "data": data, "name": file.filename or "file", "mime": mime}
    try:
        return sup.send_out(db, ids, text[:4000], by_user_id=admin.id, file=f)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/messages/{msg_id}/file")
def admin_file(msg_id: int, db: Session = Depends(get_db)):
    """Файл из переписки (через Telegram, до 20 МБ)."""
    f = sup.file_of(db, msg_id)
    if not f or not f.get("file_id"):
        raise HTTPException(404, "Файла нет")
    got = sup.tg_download(f["file_id"])
    if not got:
        raise HTTPException(502, "Telegram не отдал файл (возможно, больше 20 МБ — откройте в Telegram)")
    data, tg_name = got
    name = f.get("name") or tg_name or "file"
    mime = f.get("mime") or mimetypes.guess_type(name)[0] or ("image/jpeg" if f.get("type") == "photo" else "application/octet-stream")
    from urllib.parse import quote
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}", "Cache-Control": "private, max-age=86400"})


class TgOnly(BaseModel):
    tg: int


@router.post("/messages/read")
def admin_read(body: TgOnly, db: Session = Depends(get_db)):
    return {"read": sup.mark_read(db, body.tg)}


class Managers(BaseModel):
    tg_ids: list[int] = Field(max_length=50)


@router.get("/managers")
def admin_managers(db: Session = Depends(get_db)):
    return {"tg_ids": sup.managers(db)}


@router.put("/managers")
def admin_managers_put(body: Managers, db: Session = Depends(get_db)):
    return {"tg_ids": sup.set_managers(db, body.tg_ids)}


class AccessReq(BaseModel):
    tg: int
    action: str = Field(pattern="^(gift|trial|extend|revoke)$")
    days: int | None = Field(default=None, ge=1, le=3650)
    notify: bool = True


@router.post("/access")
def admin_access(body: AccessReq, db: Session = Depends(get_db)):
    u = db.scalar(select(models.User).where(models.User.telegram_user_id == body.tg))
    if not u:
        raise HTTPException(404, "У человека ещё нет аккаунта — пусть нажмёт /start в боте")
    sent = False
    if body.action == "revoke":
        sup.revoke(db, u)
        act.record(db, source="site", action="access:revoke", tg_id=body.tg, label="Доступ закрыт")
    else:
        days = body.days or (7 if body.action == "trial" else 30)
        kind = "trial" if body.action == "trial" else "gift"
        if body.action == "extend":
            cur = sup.access_info(db, u)
            kind = cur["kind"] if cur["kind"] in ("gift", "trial") else "gift"
        sup.grant(db, u, days, kind)
        db.refresh(u)
        lbl = {"gift": "Подарен полный доступ", "trial": "Открыт пробный период", "extend": "Доступ продлён"}[body.action]
        act.record(db, source="site", action="access:" + body.action, tg_id=body.tg, label=f"{lbl} на {days} дн.")
        if body.notify:
            txt = sup.access_text(kind, days, sup._aware(u.premium_until))
            sent = bool(sup.send_out(db, [body.tg], txt)["sent"])
    return {"access": sup.access_info(db, u), "notified": sent}


@router.get("/activity")
def admin_activity(tg: int | None = None, source: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    return {"items": act.feed(db, tg=tg, source=source, limit=limit)}


class PageView(BaseModel):
    page: str = Field(max_length=32)
    arg: str | None = Field(default=None, max_length=80)


@track_router.post("/activity")
def track(body: PageView, db: Session = Depends(get_db), user=Depends(get_current_user)):
    act.record(db, source="site", action=body.page, user_id=user.id, tg_id=user.telegram_user_id,
               label=act.site_label(body.page, body.arg))
    return {"ok": True}
