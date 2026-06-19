from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class FirmwarePresetRead(BaseModel):
    id: int
    device_model_id: int
    firmware: str
    preset_name: str
    mode: str
    hashrate: Decimal
    hashrate_unit: str
    power_w: Decimal
    efficiency_j_per_th: Decimal | None = None
    is_system: bool
    source_url: str | None = None
    notes: str | None = None

    model_config = ConfigDict(from_attributes=True)


class UserFirmwareBuildCreate(BaseModel):
    device_model_id: int
    build_name: str = Field(min_length=1, max_length=120)
    firmware: str | None = None
    mode: str | None = None
    hashrate: Decimal
    hashrate_unit: str = "TH/s"
    power_w: Decimal
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class UserFirmwareBuildRead(BaseModel):
    id: int
    user_id: int
    device_model_id: int
    build_name: str
    firmware: str | None = None
    mode: str | None = None
    hashrate: Decimal
    hashrate_unit: str
    power_w: Decimal
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
