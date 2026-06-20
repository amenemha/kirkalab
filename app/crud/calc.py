"""CRUD for calculation_runs — the FREE funnel/limits counter store."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


def count_runs(db: Session, *, user_id: int) -> int:
    """Total calculations this user has ever performed."""
    return int(
        db.scalar(
            select(func.count(models.CalculationRun.id)).where(
                models.CalculationRun.user_id == user_id
            )
        )
        or 0
    )


def count_runs_today(db: Session, *, user_id: int, now: datetime | None = None) -> int:
    """Calculations performed since 00:00 of the current UTC day."""
    now = now or datetime.now(timezone.utc)
    start_of_day = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(
        db.scalar(
            select(func.count(models.CalculationRun.id)).where(
                models.CalculationRun.user_id == user_id,
                models.CalculationRun.created_at >= start_of_day,
            )
        )
        or 0
    )


def record_run(
    db: Session,
    *,
    user_id: int,
    hashrate_ths: Decimal,
    power_w: int,
    quantity: int,
    power_price: Decimal,
    currency: str,
    net_profit_day_usdt: Decimal | None,
    device_model_id: int | None = None,
) -> models.CalculationRun:
    run = models.CalculationRun(
        user_id=user_id,
        device_model_id=device_model_id,
        hashrate_ths=hashrate_ths,
        power_w=power_w,
        quantity=quantity,
        power_price=power_price,
        currency=currency,
        net_profit_day_usdt=net_profit_day_usdt,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
