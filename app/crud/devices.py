"""CRUD repository for the ASIC catalog and device profiles."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


def list_brands(db: Session, *, active_only: bool = True) -> list[dict]:
    """Distinct brands with a model count, ordered by brand name.

    Powers the bot's first catalog screen (brand picker)."""
    stmt = select(
        models.DeviceModel.brand,
        func.count(models.DeviceModel.id),
    )
    if active_only:
        stmt = stmt.where(models.DeviceModel.is_active.is_(True))
    stmt = stmt.group_by(models.DeviceModel.brand).order_by(
        models.DeviceModel.brand
    )
    return [
        {"brand": brand, "model_count": count}
        for brand, count in db.execute(stmt).all()
    ]


def count_models_by_brand(
    db: Session, *, brand: str, active_only: bool = True
) -> int:
    stmt = select(func.count(models.DeviceModel.id)).where(
        models.DeviceModel.brand == brand
    )
    if active_only:
        stmt = stmt.where(models.DeviceModel.is_active.is_(True))
    return int(db.scalar(stmt) or 0)


def list_models_by_brand(
    db: Session,
    *,
    brand: str,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100,
) -> list[models.DeviceModel]:
    stmt = select(models.DeviceModel).where(models.DeviceModel.brand == brand)
    if active_only:
        stmt = stmt.where(models.DeviceModel.is_active.is_(True))
    stmt = (
        stmt.order_by(
            models.DeviceModel.model_name, models.DeviceModel.variant
        )
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def list_device_models(
    db: Session,
    *,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100,
) -> list[models.DeviceModel]:
    stmt = select(models.DeviceModel)
    if active_only:
        stmt = stmt.where(models.DeviceModel.is_active.is_(True))
    stmt = stmt.order_by(models.DeviceModel.brand, models.DeviceModel.model_name)
    stmt = stmt.offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


def get_device_model(db: Session, model_id: int) -> models.DeviceModel | None:
    return db.get(models.DeviceModel, model_id)


def get_device_model_by_name(
    db: Session, brand: str, model_name: str
) -> models.DeviceModel | None:
    return db.scalar(
        select(models.DeviceModel).where(
            models.DeviceModel.brand == brand,
            models.DeviceModel.model_name == model_name,
        )
    )


def list_device_profiles(
    db: Session,
    *,
    owner_user_id: int | None = None,
    include_system: bool = True,
    skip: int = 0,
    limit: int = 100,
) -> list[models.DeviceProfile]:
    stmt = select(models.DeviceProfile)
    conditions = []
    if owner_user_id is not None:
        conditions.append(models.DeviceProfile.owner_user_id == owner_user_id)
    if include_system:
        conditions.append(models.DeviceProfile.profile_type == "system")
    if conditions:
        from sqlalchemy import or_

        stmt = stmt.where(or_(*conditions))
    stmt = stmt.offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


def get_device_profile(db: Session, profile_id: int) -> models.DeviceProfile | None:
    return db.get(models.DeviceProfile, profile_id)


def create_device_profile(
    db: Session,
    *,
    name: str,
    hashrate_ths,
    power_w: int,
    profile_type: str = "system",
    owner_user_id: int | None = None,
    base_model_id: int | None = None,
    cooling_type: str | None = None,
    is_public: bool = False,
) -> models.DeviceProfile:
    profile = models.DeviceProfile(
        name=name,
        hashrate_ths=hashrate_ths,
        power_w=power_w,
        profile_type=profile_type,
        owner_user_id=owner_user_id,
        base_model_id=base_model_id,
        cooling_type=cooling_type,
        is_public=is_public,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def delete_device_profile(db: Session, profile: models.DeviceProfile) -> None:
    db.delete(profile)
    db.commit()
