"""Calc service: bridges the API/bot layer to the pure calc core.

Pulls market data via the market service (cached/fallback) and runs the pure
calculation. Returns a plain dataclass result plus the capture time of the
market snapshot used.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.schemas.calc import CalcRequest
from app.services.calc.core import MiningInput, MiningResult, calculate
from app.services.market.service import get_market_data


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
