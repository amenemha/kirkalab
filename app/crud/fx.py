"""CRUD repository for currencies and fx rates."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


def list_currencies(
    db: Session, *, active_only: bool = True
) -> list[models.Currency]:
    stmt = select(models.Currency).order_by(models.Currency.sort_order)
    if active_only:
        stmt = stmt.where(models.Currency.is_active.is_(True))
    return list(db.scalars(stmt).all())


def get_currency(db: Session, code: str) -> models.Currency | None:
    return db.get(models.Currency, code)


def add_fx_rate(
    db: Session,
    *,
    base_currency: str,
    quote_currency: str,
    rate: Decimal,
    source: str = "coingecko",
) -> models.FxRate:
    row = models.FxRate(
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate=rate,
        source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_latest_fx_rate(
    db: Session, *, base_currency: str, quote_currency: str
) -> models.FxRate | None:
    """Most recent stored rate for ``1 base = ? quote`` (None if never seen)."""
    return db.scalar(
        select(models.FxRate)
        .where(
            models.FxRate.base_currency == base_currency,
            models.FxRate.quote_currency == quote_currency,
        )
        .order_by(models.FxRate.fetched_at.desc(), models.FxRate.id.desc())
        .limit(1)
    )
