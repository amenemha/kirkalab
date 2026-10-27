"""Админка: кто пользуется ботом и сайтом, что смотрели; журнал страниц сайта."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_admin, get_current_user
from app.db.session import get_db
from app.services import activity as act

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])
track_router = APIRouter(tags=["activity"])


@router.get("/users")
def admin_users(db: Session = Depends(get_db)):
    rows = act.users_summary(db)
    return {"users": rows, "total": len(rows), "started": sum(r["started"] for r in rows),
            "saw_farm": sum(r["saw_farm"] for r in rows), "on_site": sum(1 for r in rows if r["site_logins"] or r["site_views"])}


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
