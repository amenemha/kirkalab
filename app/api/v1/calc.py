from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.calc import CalcRequest, CalcResponse
from app.services.calc.service import run_calc
from app.services.market.service import MarketUnavailableError

router = APIRouter(prefix="/calc", tags=["calc"])


@router.post("/", response_model=CalcResponse)
def calculate_profitability(
    req: CalcRequest,
    db: Session = Depends(get_db),
) -> CalcResponse:
    try:
        result, captured_at = run_calc(db, req)
    except MarketUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return CalcResponse(
        btc_per_day=result.btc_per_day,
        gross_revenue_usdt_day=result.gross_revenue_usdt_day,
        pool_revenue_usdt_day=result.pool_revenue_usdt_day,
        power_cost_day=result.power_cost_day,
        net_profit_day=result.net_profit_day,
        net_profit_month=result.net_profit_month,
        net_profit_year=result.net_profit_year,
        roi_days=result.roi_days,
        break_even_power_price=result.break_even_power_price,
        btc_price_usdt=result.btc_price_usdt,
        network_difficulty=result.network_difficulty,
        block_reward_btc=result.block_reward_btc,
        market_captured_at=captured_at.isoformat(),
        input=req,
    )
