"""Billing activation: turn a Telegram Stars payment into PRO time.

The flow is driven by the bot's ``successful_payment`` update, relayed to
``POST /internal/billing/activate``. This module holds the pure-ish business
logic (only the ORM + a DB session, no FastAPI/aiogram) so it is unit-testable
without the web or bot layers.

Idempotency: keyed on ``telegram_payment_charge_id`` (UNIQUE in the schema). A
repeat call with the same charge id is a no-op that returns the existing
subscription — Telegram retries ``successful_payment`` and must never double the
entitlement.

Renewal: if the user already has time left, the new period stacks on top
(``max(now, premium_until)`` + ``period_days``) rather than resetting it, so
buying again before expiry never loses paid days.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class BillingError(Exception):
    """Raised when activation cannot proceed (unknown/inactive/non-PRO plan)."""


def get_active_plans(db: Session) -> list[models.Plan]:
    """All purchasable plans (active, with a real Stars price), ordered."""
    return list(
        db.scalars(
            select(models.Plan)
            .where(models.Plan.is_active.is_(True))
            .order_by(models.Plan.sort_order, models.Plan.code)
        ).all()
    )


def get_plan(db: Session, plan_code: str) -> models.Plan | None:
    return db.get(models.Plan, plan_code)


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def activate_subscription(
    db: Session,
    *,
    user: models.User,
    plan_code: str,
    telegram_payment_charge_id: str,
    total_amount: int,
    now: datetime | None = None,
) -> models.Subscription:
    """Create or renew a PRO subscription from a completed Stars payment.

    Idempotent on ``telegram_payment_charge_id``. Sets ``user.is_pro`` and
    ``user.premium_until`` to the new expiry. Returns the subscription row
    (the existing one untouched on a repeat charge)."""
    existing = db.scalar(
        select(models.Subscription).where(
            models.Subscription.telegram_payment_charge_id
            == telegram_payment_charge_id
        )
    )
    if existing is not None:
        return existing

    plan = get_plan(db, plan_code)
    if plan is None or not plan.is_active:
        raise BillingError(f"Unknown or inactive plan: {plan_code}")
    if not plan.period_days:
        # The FREE plan (no period) is not purchasable.
        raise BillingError(f"Plan is not purchasable: {plan_code}")

    moment = _now(now)
    # Stack on remaining time if the user is still PRO; otherwise start now.
    base = moment
    if user.premium_until is not None:
        current_expiry = _as_aware(user.premium_until)
        if current_expiry > moment:
            base = current_expiry
    expires_at = base + timedelta(days=plan.period_days)

    sub = models.Subscription(
        user_id=user.id,
        plan_code=plan.code,
        status="active",
        started_at=moment,
        expires_at=expires_at,
        telegram_payment_charge_id=telegram_payment_charge_id,
        total_amount=int(total_amount),
    )
    db.add(sub)

    user.is_pro = True
    user.premium_until = expires_at
    db.add(user)

    db.commit()
    db.refresh(sub)
    db.refresh(user)
    return sub
