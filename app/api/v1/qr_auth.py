import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import _token_subject
from app.core.config import get_settings
from app.core.limiter import limiter
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
logger = logging.getLogger("app.qr_auth")
router = APIRouter(prefix="/auth/qr", tags=["auth-qr"])

# Telegram user IDs are positive 64-bit integers. They are nowhere near the
# top of that range today, but BigInteger is the storage ceiling, so we reject
# anything outside (0, 2**63) as malformed.
_TELEGRAM_ID_MIN = 1
_TELEGRAM_ID_MAX = 2**63 - 1


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
@limiter.limit("10/minute")
def start_qr_session(request: Request, db: Session = Depends(get_db)) -> QrStartResponse:
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
@limiter.limit("10/minute")
def approve_qr_session(
    request: Request,
    payload: QrApproveRequest,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> QrApproveResponse:
    # TRUST MODEL: this endpoint is the bridge between the Telegram bot and the
    # backend. The only party permitted to call it is *our* bot, which proves
    # its identity by presenting BOT_INTERNAL_SECRET in the X-Bot-Secret
    # header. We therefore trust the bot's assertion of which telegram_user_id
    # approved the session — the bot, not this endpoint, authenticates the
    # Telegram user (Telegram has already verified them before the bot ever
    # sees the message). Consequences:
    #   * The shared secret is the entire perimeter, so it is compared in
    #     constant time and a missing/empty configured secret denies all calls.
    #   * telegram_user_id is still validated for shape (defence in depth), but
    #     its *authenticity* rests on the bot's authenticated channel.
    #   * The rate limit on this route caps brute-force attempts against both
    #     the secret and session_id guessing.
    expected = settings.bot_internal_secret
    # Constant-time comparison avoids leaking the secret via timing. compare_digest
    # is short-circuited only when the secret is unset or the header is absent,
    # neither of which reveals anything about a configured secret's value.
    if not expected or not x_bot_secret or not secrets.compare_digest(x_bot_secret, expected):
        logger.warning(
            "QR approve rejected: invalid bot secret (session_id=%s)",
            payload.session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bot secret"
        )

    if not _TELEGRAM_ID_MIN <= payload.telegram_user_id <= _TELEGRAM_ID_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid telegram_user_id",
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
