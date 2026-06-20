"""Entitlement: the single place that decides whether a user is PRO *now*.

``users.is_pro`` is the fast flag, but the authoritative timing lives in
``users.premium_until``. Lazy expiration reconciles the two on read: if the
stored flag says PRO but the date has passed, the flag is flipped back to FREE
(and persisted) so every downstream check — the funnel, the limits, the
profile — sees the truth without needing a cron.

Keep this dependency-light (only the ORM + a DB session) so the funnel/limits
and the API can share one definition of "is this user PRO".
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db import models


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    # SQLite round-trips naive datetimes; treat them as UTC for comparison.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_pro(user: models.User, *, now: datetime | None = None) -> bool:
    """Whether the user currently holds PRO, honouring ``premium_until``.

    Pure (no writes). A user is PRO when ``is_pro`` is set AND either there is
    no expiry recorded (legacy/manual PRO) or the expiry is still in the future.
    """
    if not user.is_pro:
        return False
    if user.premium_until is None:
        return True
    return _as_aware(user.premium_until) > _now(now)


def reconcile_user(
    db: Session, user: models.User, *, now: datetime | None = None
) -> models.User:
    """Flip a lapsed PRO flag back to FREE and persist it (lazy expiration).

    Idempotent and cheap: only writes when the stored flag disagrees with the
    expiry. Returns the (possibly updated) user. Call this wherever entitlement
    is read for a request (profile, calc status, calc run)."""
    current = is_pro(user, now=now)
    if user.is_pro and not current:
        user.is_pro = False
        # Mark the lapsed subscription(s) as expired so history is accurate.
        for sub in user_active_subscriptions(db, user_id=user.id):
            sub.status = "expired"
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def user_active_subscriptions(
    db: Session, *, user_id: int
) -> list[models.Subscription]:
    from sqlalchemy import select

    return list(
        db.scalars(
            select(models.Subscription).where(
                models.Subscription.user_id == user_id,
                models.Subscription.status == "active",
            )
        ).all()
    )


def expire_subscriptions(db: Session, *, now: datetime | None = None) -> int:
    """Sweep: expire every active subscription past its ``expires_at`` and clear
    the matching ``users.is_pro``. Returns the count expired.

    Not required for correctness (entitlement is lazy on read), but provided as
    a hook for an optional scheduler/admin action so PRO flags don't linger in
    the DB for users who never come back."""
    from sqlalchemy import select

    moment = _now(now)
    expired = 0
    subs = db.scalars(
        select(models.Subscription).where(
            models.Subscription.status == "active",
            models.Subscription.expires_at.is_not(None),
            models.Subscription.expires_at < moment,
        )
    ).all()
    for sub in subs:
        sub.status = "expired"
        user = db.get(models.User, sub.user_id)
        if user is not None and not _has_other_active(
            db, user_id=user.id, exclude_id=sub.id, now=moment
        ):
            user.is_pro = False
            db.add(user)
        db.add(sub)
        expired += 1
    if expired:
        db.commit()
    return expired


def _has_other_active(
    db: Session, *, user_id: int, exclude_id: int, now: datetime
) -> bool:
    from sqlalchemy import select

    rows = db.scalars(
        select(models.Subscription).where(
            models.Subscription.user_id == user_id,
            models.Subscription.id != exclude_id,
            models.Subscription.status == "active",
        )
    ).all()
    return any(
        r.expires_at is None or _as_aware(r.expires_at) > now for r in rows
    )
