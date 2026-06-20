"""Schemas for the billing (Telegram Stars PRO) internal API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlanOut(BaseModel):
    """A purchasable/active plan as shown to the bot's plan picker."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    period_days: int | None = None
    price_stars: int
    currency: str = "XTR"
    features_json: list[str] | None = None
    limits_json: dict | None = None
    is_active: bool = True
    sort_order: int = 0


class PlansResponse(BaseModel):
    plans: list[PlanOut]


class BillingActivateRequest(BaseModel):
    """Relayed by the bot after a successful Telegram Stars payment.

    ``total_amount`` is integer Stars (XTR); ``telegram_payment_charge_id`` is
    the idempotency key. The user is identified by telegram id."""

    telegram_id: int
    plan_code: str
    telegram_payment_charge_id: str = Field(min_length=1)
    total_amount: int = 0

    model_config = ConfigDict(extra="forbid")


class SubscriptionState(BaseModel):
    """The user's PRO state after an activation."""

    is_pro: bool
    plan_code: str
    status: str
    started_at: datetime | None = None
    expires_at: datetime | None = None
    premium_until: datetime | None = None
    # True when this exact charge had already been applied (idempotent repeat).
    already_applied: bool = False
