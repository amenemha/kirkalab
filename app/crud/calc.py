"""CRUD for calculation_runs — the FREE funnel/limits counter store and the
backing query for the "Мои отчёты / История" screen (Queue 2.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


def history_cutoff(
    *, retention_days: int, now: datetime | None = None
) -> datetime | None:
    """The earliest ``created_at`` a history row may have to be shown, or None.

    ``retention_days <= 0`` means "no limit" (PRO / unbounded retention) and
    returns None. Otherwise the cutoff is ``now - retention_days`` (a rolling
    72h-style window when retention_days=3), so older rows are filtered out of
    the *view* — they are never physically deleted here."""
    if retention_days <= 0:
        return None
    moment = now or datetime.now(timezone.utc)
    return moment - timedelta(days=retention_days)


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
    net_profit_month_usdt: Decimal | None = None,
    device_model_id: int | None = None,
    device_name: str | None = None,
) -> models.CalculationRun:
    run = models.CalculationRun(
        user_id=user_id,
        device_model_id=device_model_id,
        device_name=device_name,
        hashrate_ths=hashrate_ths,
        power_w=power_w,
        quantity=quantity,
        power_price=power_price,
        currency=currency,
        net_profit_day_usdt=net_profit_day_usdt,
        net_profit_month_usdt=net_profit_month_usdt,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def count_history(
    db: Session, *, user_id: int, cutoff: datetime | None = None
) -> int:
    """How many of the user's runs are visible under the retention ``cutoff``.

    ``cutoff`` is the earliest allowed ``created_at`` (see :func:`history_cutoff`);
    None means no retention limit. Used to drive pagination and the "история
    обрезана" PRO hint."""
    stmt = select(func.count(models.CalculationRun.id)).where(
        models.CalculationRun.user_id == user_id
    )
    if cutoff is not None:
        stmt = stmt.where(models.CalculationRun.created_at >= cutoff)
    return int(db.scalar(stmt) or 0)


def list_history(
    db: Session,
    *,
    user_id: int,
    cutoff: datetime | None = None,
    offset: int = 0,
    limit: int = 5,
) -> list[models.CalculationRun]:
    """A page of the user's saved calculations, newest first.

    Retention is enforced at the query level: rows older than ``cutoff`` are
    never returned (filtration only — nothing is deleted). ``offset``/``limit``
    drive the on-screen pagination."""
    stmt = (
        select(models.CalculationRun)
        .where(models.CalculationRun.user_id == user_id)
        .order_by(models.CalculationRun.created_at.desc(), models.CalculationRun.id.desc())
    )
    if cutoff is not None:
        stmt = stmt.where(models.CalculationRun.created_at >= cutoff)
    stmt = stmt.offset(max(offset, 0)).limit(max(limit, 1))
    return list(db.scalars(stmt).all())


def get_history_run(
    db: Session,
    *,
    user_id: int,
    run_id: int,
    cutoff: datetime | None = None,
) -> models.CalculationRun | None:
    """Fetch one run for the detail screen, scoped to the user and retention.

    Returns None when the run does not belong to the user or has fallen outside
    the retention window (so expired rows can't be opened directly)."""
    stmt = select(models.CalculationRun).where(
        models.CalculationRun.id == run_id,
        models.CalculationRun.user_id == user_id,
    )
    if cutoff is not None:
        stmt = stmt.where(models.CalculationRun.created_at >= cutoff)
    return db.scalar(stmt)
