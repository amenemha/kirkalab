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

    # Passport card
    series: str | None = None
    variant: str | None = None
    hashrate_unit: str | None = None
    efficiency_j_per_th: Decimal | None = None
    cooling_type: str | None = None
    release_year: int | None = None
    voltage_input: str | None = None
    noise_db: Decimal | None = None
    operating_temp: str | None = None
    dimensions_mm: str | None = None
    weight_kg: Decimal | None = None
    chip: str | None = None
    network: str | None = None
    max_hashrate_note: str | None = None
    source_url: str | None = None
    notes: str | None = None

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
