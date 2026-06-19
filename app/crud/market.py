"""CRUD repository for market snapshots."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


def add_snapshot(
    db: Session,
    *,
    source: str,
    network_difficulty: Decimal,
    block_reward_btc: Decimal,
    price_usdt: Decimal,
    coin_code: str = "BTC",
) -> models.MarketSnapshot:
    snapshot = models.MarketSnapshot(
        source=source,
        coin_code=coin_code,
        network_difficulty=network_difficulty,
        block_reward_btc=block_reward_btc,
        price_usdt=price_usdt,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def get_latest_snapshot(
    db: Session, coin_code: str = "BTC"
) -> models.MarketSnapshot | None:
    return db.scalar(
        select(models.MarketSnapshot)
        .where(models.MarketSnapshot.coin_code == coin_code)
        .order_by(models.MarketSnapshot.captured_at.desc())
        .limit(1)
    )
