"""CRUD repository for firmware presets and user firmware builds."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


def list_presets(
    db: Session,
    *,
    device_model_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[models.FirmwarePreset]:
    stmt = select(models.FirmwarePreset)
    if device_model_id is not None:
        stmt = stmt.where(
            models.FirmwarePreset.device_model_id == device_model_id
        )
    stmt = stmt.order_by(
        models.FirmwarePreset.device_model_id,
        models.FirmwarePreset.firmware,
        models.FirmwarePreset.preset_name,
    ).offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


def list_user_builds(
    db: Session, *, user_id: int, skip: int = 0, limit: int = 100
) -> list[models.UserFirmwareBuild]:
    stmt = (
        select(models.UserFirmwareBuild)
        .where(models.UserFirmwareBuild.user_id == user_id)
        .order_by(models.UserFirmwareBuild.id)
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def get_user_build(
    db: Session, *, build_id: int, user_id: int
) -> models.UserFirmwareBuild | None:
    build = db.get(models.UserFirmwareBuild, build_id)
    if build is None or build.user_id != user_id:
        return None
    return build


def create_user_build(
    db: Session,
    *,
    user_id: int,
    device_model_id: int,
    build_name: str,
    hashrate: Decimal,
    power_w: Decimal,
    firmware: str | None = None,
    mode: str | None = None,
    hashrate_unit: str = "TH/s",
    notes: str | None = None,
) -> models.UserFirmwareBuild:
    build = models.UserFirmwareBuild(
        user_id=user_id,
        device_model_id=device_model_id,
        build_name=build_name,
        firmware=firmware,
        mode=mode,
        hashrate=hashrate,
        hashrate_unit=hashrate_unit,
        power_w=power_w,
        notes=notes,
    )
    db.add(build)
    db.commit()
    db.refresh(build)
    return build


def delete_user_build(db: Session, build: models.UserFirmwareBuild) -> None:
    db.delete(build)
    db.commit()
