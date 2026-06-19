import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import _token_subject
from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.crud import users as crud_users
from app.db import models
from app.db.session import get_db
from app.schemas.users import (
    QrApproveRequest,
    QrApproveResponse,
    QrStartResponse,
    QrStatusResponse,
)

settings = get_settings()
router = APIRouter(prefix="/auth/qr", tags=["auth-qr"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    # SQLite round-trips timezone-naive datetimes; normalise to UTC-aware so
    # comparisons against an aware "now" never raise.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _is_expired(session: models.QrLoginSession) -> bool:
    return _now() >= _as_aware(session.expires_at)


@router.post("/start", response_model=QrStartResponse)
def start_qr_session(db: Session = Depends(get_db)) -> QrStartResponse:
    session_id = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=settings.qr_session_ttl_seconds)
    session = models.QrLoginSession(
        session_id=session_id,
        status="pending",
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    deep_link = f"https://t.me/{settings.bot_username}?start=qr_{session_id}"
    return QrStartResponse(
        session_id=session_id,
        deep_link=deep_link,
        expires_at=session.expires_at,
    )


@router.get("/status/{session_id}", response_model=QrStatusResponse)
def qr_session_status(
    session_id: str, db: Session = Depends(get_db)
) -> QrStatusResponse:
    session = db.scalar(
        select(models.QrLoginSession).where(
            models.QrLoginSession.session_id == session_id
        )
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    # Already consumed: the token was handed out once and never again.
    if session.status == "consumed":
        return QrStatusResponse(status="consumed")

    # Expire pending/approved sessions that have run out of time.
    if session.status in ("pending", "approved") and _is_expired(session):
        if session.status != "expired":
            session.status = "expired"
            db.add(session)
            db.commit()
        return QrStatusResponse(status="expired")

    if session.status == "approved":
        user = crud_users.get_user(db, user_id=session.user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not available",
            )
        subject = _token_subject(user)
        access_token = create_access_token(subject)
        refresh_token = create_refresh_token(subject)
        session.status = "consumed"
        db.add(session)
        db.commit()
        return QrStatusResponse(
            status="approved",
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    return QrStatusResponse(status=session.status)


@router.post("/approve", response_model=QrApproveResponse)
def approve_qr_session(
    payload: QrApproveRequest,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> QrApproveResponse:
    expected = settings.bot_internal_secret
    if not expected or not x_bot_secret or not secrets.compare_digest(x_bot_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bot secret"
        )

    session = db.scalar(
        select(models.QrLoginSession).where(
            models.QrLoginSession.session_id == payload.session_id
        )
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    if session.status in ("pending", "approved") and _is_expired(session):
        session.status = "expired"
        db.add(session)
        db.commit()

    if session.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is not pending (status: {session.status})",
        )

    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=payload.telegram_user_id
    )
    session.status = "approved"
    session.telegram_user_id = payload.telegram_user_id
    session.user_id = user.id
    db.add(session)
    db.commit()
    return QrApproveResponse(status="approved")
