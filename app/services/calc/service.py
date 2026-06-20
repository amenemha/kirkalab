"""Calc service: bridges the API/bot layer to the pure calc core.

Pulls market data via the market service (cached/fallback) and runs the pure
calculation. Returns a plain dataclass result plus the capture time of the
market snapshot used.
"""
from __future__ import annotations

from datetime import datetime

from decimal import Decimal

from sqlalchemy.orm import Session

from app.db import models
from app.schemas.calc import CalcRequest, CompareRequest
from app.services.calc.core import (
    CompareResult,
    MiningInput,
    MiningResult,
    calculate,
    compare,
)
from app.services.market.service import get_market_data


def resolve_model_specs(db: Session, device_model_id: int) -> tuple[Decimal, int]:
    """Resolve a catalog model's (hashrate_ths, power_w) for the calc core.

    Raises ``ValueError`` if the model does not exist so the API/internal layer
    can surface a 404/422 rather than a 500."""
    model = db.get(models.DeviceModel, device_model_id)
    if model is None:
        raise ValueError("device_model not found")
    return Decimal(model.default_hashrate_ths), int(model.default_power_w)


def run_calc(db: Session, req: CalcRequest) -> tuple[MiningResult, datetime]:
    market, captured_at = get_market_data(db, coin_code="BTC")
    inp = MiningInput(
        hashrate_ths=req.hashrate_ths,
        power_w=req.power_w,
        quantity=req.quantity,
        power_price=req.power_price,
        pool_fee_pct=req.pool_fee_pct,
        uptime_pct=req.uptime_pct,
        hardware_cost=req.hardware_cost,
    )
    result = calculate(inp, market)
    return result, captured_at


def _resolve_custom_specs(
    db: Session, req: CompareRequest
) -> tuple[Decimal, int]:
    """Resolve the custom side's (hashrate_ths, power_w).

    Precedence: saved build > system preset > explicit overrides."""
    if req.user_firmware_build_id is not None:
        build = db.get(models.UserFirmwareBuild, req.user_firmware_build_id)
        if build is None:
            raise ValueError("user_firmware_build not found")
        return Decimal(build.hashrate), int(build.power_w)

    if req.firmware_preset_id is not None:
        preset = db.get(models.FirmwarePreset, req.firmware_preset_id)
        if preset is None:
            raise ValueError("firmware_preset not found")
        return Decimal(preset.hashrate), int(preset.power_w)

    # Validated to be present in this branch by the schema.
    return req.custom_hashrate_ths, int(req.custom_power_w)


def run_compare(
    db: Session, req: CompareRequest
) -> tuple[CompareResult, datetime]:
    market, captured_at = get_market_data(db, coin_code="BTC")
    custom_hashrate, custom_power = _resolve_custom_specs(db, req)

    shared = dict(
        quantity=req.quantity,
        power_price=req.power_price,
        pool_fee_pct=req.pool_fee_pct,
        uptime_pct=req.uptime_pct,
        hardware_cost=req.hardware_cost,
    )
    base_inp = MiningInput(
        hashrate_ths=req.hashrate_ths, power_w=req.power_w, **shared
    )
    custom_inp = MiningInput(
        hashrate_ths=custom_hashrate, power_w=custom_power, **shared
    )
    result = compare(base_inp, custom_inp, market)
    return result, captured_at
