"""Kirka pools: observer links (ViaBTC first), hourly hashrate archive, daily accruals, workers.

ViaBTC keeps only 72 hours of hourly data, so Kirka archives every hour itself; the
daily table for any time zone (MSK by default) is built from that archive.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import farm_models  # noqa: F401  (farm_clients for the FK)
from app.db.session import Base


class PoolAccount(Base):
    __tablename__ = "pool_accounts"
    __table_args__ = (UniqueConstraint("provider", "access_key", "coin", "sub_user_id", name="uq_pool_acc"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="viabtc", nullable=False)
    access_key: Mapped[str] = mapped_column(String(128), nullable=False)
    coin: Mapped[str] = mapped_column(String(16), default="BTC", nullable=False)
    sub_user_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    account: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text)
    group_name: Mapped[str | None] = mapped_column(String(128))  # лист таблицы: площадка/клиент
    note_user: Mapped[str | None] = mapped_column(String(128))   # user_name из таблицы
    tg_chat: Mapped[str | None] = mapped_column(String(32))
    tg_thread: Mapped[int | None] = mapped_column(Integer)
    threshold_ths: Mapped[float | None] = mapped_column(Float)
    track: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("farm_clients.id"))
    # last snapshot (H/s)
    hs_10m: Mapped[float | None] = mapped_column(Float)
    hs_1h: Mapped[float | None] = mapped_column(Float)
    hs_1d: Mapped[float | None] = mapped_column(Float)
    workers_active: Mapped[int | None] = mapped_column(Integer)
    workers_inactive: Mapped[int | None] = mapped_column(Integer)
    workers_invalid: Mapped[int | None] = mapped_column(Integer)
    balance: Mapped[float | None] = mapped_column(Float)
    profit_24h: Mapped[float | None] = mapped_column(Float)
    profit_total: Mapped[float | None] = mapped_column(Float)
    last_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)  # что ссылка не показывает (начисления/воркеры)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PoolHourly(Base):
    __tablename__ = "pool_hourly"
    account_id: Mapped[int] = mapped_column(ForeignKey("pool_accounts.id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # unix, start of UTC hour
    hs: Mapped[float] = mapped_column(Float, nullable=False)
    workers: Mapped[int | None] = mapped_column(Integer)
    reject: Mapped[float | None] = mapped_column(Float)


class PoolDaily(Base):
    """Pool's own daily rows (ViaBTC profit/detail): hashrate and accrual per pool day."""
    __tablename__ = "pool_daily"
    account_id: Mapped[int] = mapped_column(ForeignKey("pool_accounts.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    hs: Mapped[float | None] = mapped_column(Float)
    profit: Mapped[float | None] = mapped_column(Float)
    pps: Mapped[float | None] = mapped_column(Float)
    pplns: Mapped[float | None] = mapped_column(Float)
    fpps_rate: Mapped[float | None] = mapped_column(Float)
    unit_output: Mapped[float | None] = mapped_column(Float)
    price_usd: Mapped[float | None] = mapped_column(Float)
    money_usd: Mapped[float | None] = mapped_column(Float)


class PoolWorker(Base):
    __tablename__ = "pool_workers"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_pool_worker"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("pool_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(256), index=True)  # account.worker — как в CROFT
    status: Mapped[str | None] = mapped_column(String(16))
    hs_10m: Mapped[float | None] = mapped_column(Float)
    hs_1h: Mapped[float | None] = mapped_column(Float)
    hs_1d: Mapped[float | None] = mapped_column(Float)
    reject_1h: Mapped[float | None] = mapped_column(Float)
    online_7d_h: Mapped[float | None] = mapped_column(Float)
    last_active: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
