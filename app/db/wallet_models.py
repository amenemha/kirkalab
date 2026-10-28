"""Наблюдение кошельков: адреса людей и входящие переводы."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

JSONType = JSONB().with_variant(JSON(), "sqlite")


class WalletWatch(Base):
    __tablename__ = "wallet_watch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(8), nullable=False)          # BTC LTC DOGE TRON TON ETH BSC SOL
    address: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(80))
    muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    min_usd: Mapped[float] = mapped_column(Float, default=1.0, server_default="1", nullable=False)
    cursor: Mapped[str | None] = mapped_column(String(200))                 # BSC: last block; others: unused
    balance: Mapped[dict | None] = mapped_column(JSONType)                  # {"BNB": 0.1} for balance-diff chains
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (UniqueConstraint("user_id", "chain", "address", name="uq_wallet_watch"),)


class WalletTx(Base):
    __tablename__ = "wallet_tx"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(Integer, ForeignKey("wallet_watch.id", ondelete="CASCADE"), nullable=False)
    uid: Mapped[str] = mapped_column(String(220), nullable=False)          # txid[:log/token]
    txid: Mapped[str] = mapped_column(String(160), nullable=False)
    token: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    usd: Mapped[float | None] = mapped_column(Float)
    rub: Mapped[float | None] = mapped_column(Float)
    from_addr: Mapped[str | None] = mapped_column(String(128))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)        # ok | dust | spam | poison | zero
    notified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    first: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)  # было до подключения
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("watch_id", "uid", name="uq_wallet_tx"), Index("ix_wallet_tx_watch_at", "watch_id", "at"))
