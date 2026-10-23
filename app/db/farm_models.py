"""Kirka farm: sites, containers, hosting clients, ASIC devices and their history.

Data comes from CROFT (read-only) via app.services.farm.sync. The current state
of each ASIC lives in ``farm_devices``; every status/alarm/worker/pool/IP change
is appended to ``farm_device_events`` (the "Автотека" history); hourly hashrate
buckets go to ``farm_device_hourly`` and per-container snapshots to
``farm_container_samples``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

JSONType = JSONB().with_variant(Text(), "sqlite")


class FarmSite(Base):
    __tablename__ = "farm_sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="croft", nullable=False)
    ext_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FarmContainer(Base):
    __tablename__ = "farm_containers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ext_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("farm_sites.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FarmClient(Base):
    __tablename__ = "farm_clients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ext_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FarmDevice(Base):
    __tablename__ = "farm_devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ext_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    mac: Mapped[str | None] = mapped_column(String(32), index=True)
    serial: Mapped[str | None] = mapped_column(String(128), index=True)
    user_number: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    algo: Mapped[str | None] = mapped_column(String(32))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("farm_sites.id"), index=True)
    container_id: Mapped[int | None] = mapped_column(ForeignKey("farm_containers.id"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("farm_clients.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    in_operation: Mapped[bool] = mapped_column(Boolean, default=True)
    ip: Mapped[str | None] = mapped_column(String(64), index=True)
    worker: Mapped[str | None] = mapped_column(String(255), index=True)
    pool_url: Mapped[str | None] = mapped_column(String(255))
    power_mode: Mapped[str | None] = mapped_column(String(24))
    hashrate_ths: Mapped[float | None] = mapped_column(Float)
    hashrate_5m_ths: Mapped[float | None] = mapped_column(Float)
    nominal_ths: Mapped[float | None] = mapped_column(Float)
    power_w: Mapped[float | None] = mapped_column(Float)
    temp_env: Mapped[float | None] = mapped_column(Float)
    uptime_sec: Mapped[int | None] = mapped_column(BigInteger)
    alarm_count: Mapped[int] = mapped_column(Integer, default=0)
    worst_level: Mapped[int] = mapped_column(Integer, default=-1, index=True)
    alarms: Mapped[list | None] = mapped_column(JSONType)
    tags: Mapped[list | None] = mapped_column(JSONType)
    pools: Mapped[list | None] = mapped_column(JSONType)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    online_sec_total: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FarmDeviceEvent(Base):
    __tablename__ = "farm_device_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("farm_devices.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # status|alarm_on|alarm_off|worker|pool|ip|container|client|seen
    old: Mapped[str | None] = mapped_column(Text)
    new: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(32), index=True)
    cls: Mapped[str | None] = mapped_column(String(32), index=True)
    level: Mapped[int | None] = mapped_column(Integer)
    author: Mapped[str | None] = mapped_column(String(64))  # 'croft' or 'tg:<id>'
    __table_args__ = (Index("ix_farm_events_dev_at", "device_id", "at"),)


class FarmDeviceHourly(Base):
    __tablename__ = "farm_device_hourly"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("farm_devices.id", ondelete="CASCADE"))
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    samples: Mapped[int] = mapped_column(Integer, default=0)
    online_samples: Mapped[int] = mapped_column(Integer, default=0)
    hashrate_sum: Mapped[float] = mapped_column(Float, default=0.0)
    power_sum: Mapped[float] = mapped_column(Float, default=0.0)
    temp_max: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("device_id", "hour", name="uq_farm_hourly_dev_hour"),)


class FarmContainerSample(Base):
    __tablename__ = "farm_container_samples"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    container_id: Mapped[int] = mapped_column(ForeignKey("farm_containers.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    total: Mapped[int] = mapped_column(Integer)
    online: Mapped[int] = mapped_column(Integer)
    offline: Mapped[int] = mapped_column(Integer)
    hashrate_ths: Mapped[float] = mapped_column(Float)
    power_kw: Mapped[float] = mapped_column(Float)


class FarmIncident(Base):
    """Mass outage (авария): ≥80% of a container/site goes offline within 5 min."""
    __tablename__ = "farm_incidents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))  # container|site
    scope_id: Mapped[int] = mapped_column(Integer, index=True)
    scope_name: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    devices_total: Mapped[int] = mapped_column(Integer)
    devices_down: Mapped[int] = mapped_column(Integer)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)


class FarmSyncRun(Base):
    __tablename__ = "farm_sync_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    devices: Mapped[int] = mapped_column(Integer, default=0)
    events: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
