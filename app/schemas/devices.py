from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DeviceModelRead(BaseModel):
    id: int
    brand: str
    model_name: str
    algorithm: str
    coin_family: str
    default_hashrate_ths: Decimal
    default_power_w: int
    released_at: date | None = None
    is_active: bool
    data_quality: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeviceProfileRead(BaseModel):
    id: int
    owner_user_id: int | None
    base_model_id: int | None
    profile_type: str
    name: str
    hashrate_ths: Decimal
    power_w: int
    cooling_type: str | None
    is_public: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
