"""Журнал действий: кнопки и команды бота, страницы сайта, отправленные уведомления.

Нужен админке («кто нажал /start, что смотрел») и ленте уведомлений на сайте.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

JSONType = JSONB().with_variant(JSON(), "sqlite")


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # bot | site | notify
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300))
    meta: Mapped[dict | None] = mapped_column(JSONType)

    __table_args__ = (Index("ix_activity_tg_ts", "tg_id", "ts"),)
