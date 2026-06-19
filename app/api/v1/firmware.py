from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.crud import devices as crud_devices
from app.crud import firmware as crud_firmware
from app.db import models
from app.db.session import get_db
from app.schemas.firmware import (
    FirmwarePresetRead,
    UserFirmwareBuildCreate,
    UserFirmwareBuildRead,
)

router = APIRouter(prefix="/firmware", tags=["firmware"])


@router.get("/presets", response_model=list[FirmwarePresetRead])
def list_presets(
    device_model_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list:
    return crud_firmware.list_presets(
        db, device_model_id=device_model_id, skip=skip, limit=limit
    )


@router.get("/builds", response_model=list[UserFirmwareBuildRead])
def list_builds(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list:
    return crud_firmware.list_user_builds(
        db, user_id=current_user.id, skip=skip, limit=limit
    )


@router.post(
    "/builds",
    response_model=UserFirmwareBuildRead,
    status_code=status.HTTP_201_CREATED,
)
def create_build(
    build_in: UserFirmwareBuildCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.UserFirmwareBuild:
    # PRO-gating: saving custom builds is a PRO capability.
    if not current_user.is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Saving custom firmware builds is a PRO feature",
        )
    if crud_devices.get_device_model(db, model_id=build_in.device_model_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device model not found",
        )
    return crud_firmware.create_user_build(
        db,
        user_id=current_user.id,
        device_model_id=build_in.device_model_id,
        build_name=build_in.build_name,
        firmware=build_in.firmware,
        mode=build_in.mode,
        hashrate=build_in.hashrate,
        hashrate_unit=build_in.hashrate_unit,
        power_w=build_in.power_w,
        notes=build_in.notes,
    )


@router.get("/builds/{build_id}", response_model=UserFirmwareBuildRead)
def get_build(
    build_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.UserFirmwareBuild:
    build = crud_firmware.get_user_build(
        db, build_id=build_id, user_id=current_user.id
    )
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )
    return build


@router.delete("/builds/{build_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_build(
    build_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> Response:
    build = crud_firmware.get_user_build(
        db, build_id=build_id, user_id=current_user.id
    )
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )
    crud_firmware.delete_user_build(db, build)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
