from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.crud import devices as crud_devices
from app.db.session import get_db
from app.schemas.devices import (
    BrandRead,
    DeviceModelPage,
    DeviceModelRead,
    DeviceProfileRead,
)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("/brands", response_model=list[BrandRead])
def list_brands(
    active_only: bool = True,
    db: Session = Depends(get_db),
) -> list:
    """Distinct brands with model counts — first screen of the bot catalog."""
    return crud_devices.list_brands(db, active_only=active_only)


@router.get("/brands/{brand}/models", response_model=DeviceModelPage)
def list_models_for_brand(
    brand: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    active_only: bool = True,
    db: Session = Depends(get_db),
) -> DeviceModelPage:
    """Paginated models for a brand — second screen of the bot catalog."""
    total = crud_devices.count_models_by_brand(
        db, brand=brand, active_only=active_only
    )
    items = crud_devices.list_models_by_brand(
        db, brand=brand, active_only=active_only, skip=skip, limit=limit
    )
    return DeviceModelPage(
        brand=brand, total=total, skip=skip, limit=limit, items=items
    )


@router.get("/models", response_model=list[DeviceModelRead])
def list_models(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True,
    db: Session = Depends(get_db),
) -> list:
    return crud_devices.list_device_models(
        db, active_only=active_only, skip=skip, limit=limit
    )


@router.get("/models/{model_id}", response_model=DeviceModelRead)
def get_model(model_id: int, db: Session = Depends(get_db)):
    model = crud_devices.get_device_model(db, model_id=model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device model not found"
        )
    return model


@router.get("/profiles", response_model=list[DeviceProfileRead])
def list_profiles(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list:
    return crud_devices.list_device_profiles(
        db, include_system=True, skip=skip, limit=limit
    )
