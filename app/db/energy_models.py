"""Kirka energy: object tree (ГПП → фидер → РП → КТП → ввод → PDU → гнездо), meters,
hourly energy (почасовки энергокомпании / ручной ввод / фото), meter readings, imports,
plug reference per ASIC model.

Hours are stored as UTC timestamps of the hour start; files from the energy company are
in Moscow time (UTC+3) and are converted on import.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import farm_models  # noqa: F401  (farm_sites / farm_containers for FKs)
from app.db.session import Base

JSONType = JSONB().with_variant(JSON(), "sqlite")

NODE_KINDS = ("gpp", "feeder", "rp", "cell", "ktp", "input", "pdu", "socket", "container", "other")


class EnergyNode(Base):
    __tablename__ = "energy_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("farm_sites.id", ondelete="SET NULL"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("energy_nodes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(4))  # A/B/C, AB…, ABC
    voltage_kv: Mapped[float | None] = mapped_column(Float)
    container_id: Mapped[int | None] = mapped_column(ForeignKey("farm_containers.id", ondelete="SET NULL"), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyMeter(Base):
    __tablename__ = "energy_meters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    substation: Mapped[str | None] = mapped_column(String(128))
    feeder: Mapped[str | None] = mapped_column(String(128))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("energy_nodes.id", ondelete="SET NULL"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("farm_sites.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="commercial", nullable=False)  # commercial/technical
    ratio: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    loss_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    const_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    measure: Mapped[str] = mapped_column(String(8), default="A+", nullable=False)
    group: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyImport(Base):
    __tablename__ = "energy_imports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="hourly", nullable=False)
    meters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kwh: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    by_user: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyHourly(Base):
    __tablename__ = "energy_hourly"
    __table_args__ = (UniqueConstraint("meter_id", "hour", name="uq_energy_hourly_meter_hour"),
                      Index("ix_energy_hourly_hour", "hour"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("energy_meters.id", ondelete="CASCADE"), nullable=False)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kwh: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="file", nullable=False)  # file/manual/photo/poll
    import_id: Mapped[int | None] = mapped_column(ForeignKey("energy_imports.id", ondelete="SET NULL"))


class EnergyReading(Base):
    """Накопительное показание счётчика (ручной ввод или фото через бот)."""
    __tablename__ = "energy_readings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("energy_meters.id", ondelete="CASCADE"), index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # manual/photo
    photo: Mapped[str | None] = mapped_column(String(255))
    by_user: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AsicPlug(Base):
    """Справочник разъёмов: что стоит на асике и каким кабелем он подключается."""
    __tablename__ = "asic_plugs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # подстрока модели
    inlet: Mapped[str] = mapped_column(String(32), nullable=False)   # разъём на асике
    cord: Mapped[str | None] = mapped_column(String(64))             # кабель / вилка к PDU
    inlets: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supply: Mapped[str | None] = mapped_column(String(64))           # 220–277 В 1ф / 380–415 В 3ф
    amps: Mapped[float | None] = mapped_column(Float)
    psu: Mapped[str | None] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
