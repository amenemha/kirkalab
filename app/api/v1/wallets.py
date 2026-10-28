"""Кошельки человека: наблюдение поступлений (сайт)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.db import models
from app.db.session import get_db
from app.db.wallet_models import WalletWatch
from app.services.wallets import watch as ww
from app.services.wallets.chains import CHAINS, ORDER, detect

router = APIRouter(prefix="/me/wallets", tags=["wallets"])


def _summary(db: Session, user: models.User) -> dict:
    rows = db.scalars(select(WalletWatch).where(WalletWatch.user_id == user.id).order_by(WalletWatch.id)).all()
    return {"items": [ww.view(w, db) for w in rows], "used": len({w.address for w in rows}), "limit": ww.limit_of(user),
            "chains": [{"id": c, "name": CHAINS[c]["name"], "net": CHAINS[c]["net"]} for c in ORDER]}


@router.get("")
def my_wallets(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _summary(db, user)


@router.get("/detect")
def detect_address(address: str, user: models.User = Depends(get_current_user)):
    return {"chains": [{"id": c, "name": CHAINS[c]["name"], "net": CHAINS[c]["net"]} for c in detect(address)]}


@router.post("")
def add_wallet(body: dict, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        ws = ww.add(db, user, str(body.get("address") or ""), body.get("label"), body.get("chains") or None)
    except ww.WalletError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    from app.services import activity as act
    act.record(db, source="site", action="wallet:add", user_id=user.id, label=", ".join(f"{w.chain} {w.address}" for w in ws))
    return {"added": [ww.view(w, db) for w in ws], **_summary(db, user)}


def _own(db: Session, user: models.User, wid: int) -> WalletWatch:
    w = ww.owned(db, user, wid)
    if not w:
        raise HTTPException(status_code=404, detail="Кошелёк не найден")
    return w


@router.patch("/{wid}")
def edit_wallet(wid: int, body: dict, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _own(db, user, wid)
    if "label" in body:
        w.label = (str(body.get("label") or "").strip()[:80]) or None
    if "muted" in body:
        w.muted = bool(body.get("muted"))
    if "min_usd" in body:
        try:
            w.min_usd = max(0.0, min(1e6, float(body.get("min_usd"))))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Порог — число в долларах") from None
    db.commit()
    return ww.view(w, db)


@router.delete("/{wid}")
def del_wallet(wid: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _own(db, user, wid)
    db.delete(w)
    db.commit()
    return {"ok": True}


@router.get("/{wid}/tx")
def wallet_tx(wid: int, hidden: bool = False, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _own(db, user, wid)
    return {"wallet": ww.view(w, db), "items": ww.history(db, w, hidden)}


@router.get("/aml/check")
def aml_check(chain: str, address: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    chain = chain.upper()
    if chain not in CHAINS:
        raise HTTPException(status_code=400, detail="Неизвестная сеть")
    out = ww.aml(chain, address.strip())
    ids = [w.id for w in db.scalars(select(WalletWatch).where(WalletWatch.user_id == user.id))]
    from app.db.wallet_models import WalletTx
    out["seen"] = db.scalar(select(func.count(WalletTx.id)).where(WalletTx.watch_id.in_(ids or [0]), WalletTx.from_addr == address.strip())) or 0
    return out
