"""Подключения человека: свой CROFT и ссылки наблюдателя пулов (сайт)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.db import models
from app.db.activity_models import CroftAccount
from app.db.session import get_db
from app.services import connections as cn

router = APIRouter(prefix="/me/connections", tags=["connections"])


@router.get("")
def my_connections(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return cn.of_user(db, user)


@router.post("/croft")
def add_croft(body: dict, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        a = cn.croft_connect(db, user, str(body.get("login") or ""), str(body.get("password") or ""))
    except cn.ConnError as e:
        raise HTTPException(status_code=400, detail=e.text) from None
    cn.notify_managers(db, user, f"подключил CROFT ({a.login}) — {cn.croft_text(a.stats or {})}")
    return cn.croft_view(a)


@router.post("/croft/{acc_id}/check")
def check_croft(acc_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(CroftAccount, acc_id)
    if not a or a.user_id != user.id:
        raise HTTPException(status_code=404, detail="Подключение не найдено")
    return cn.croft_view(cn.croft_recheck(db, a))


@router.delete("/croft/{acc_id}")
def del_croft(acc_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not cn.delete_croft(db, user, acc_id):
        raise HTTPException(status_code=404, detail="Подключение не найдено")
    return {"ok": True}


@router.post("/pool")
def add_pool(body: dict, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        r = cn.pool_connect(db, user, str(body.get("url") or ""))
    except cn.ConnError as e:
        raise HTTPException(status_code=400, detail=e.text) from None
    if not r.get("already"):
        cn.notify_managers(db, user, f"добавил ссылку наблюдателя {r['name']}" + (f" {r['coin']}" if r.get("coin") else ""))
    return r


@router.delete("/pool/{acc_id}")
def del_pool(acc_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not cn.delete_pool(db, user, acc_id):
        raise HTTPException(status_code=404, detail="Ссылка не найдена")
    return {"ok": True}
