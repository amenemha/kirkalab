from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.crud import users as crud_users
from app.db import models
from app.db.session import get_db
from app.schemas.calc import (
    CalcRequest,
    CalcResponse,
    CompareDelta,
    CompareRequest,
    CompareResponse,
)
from app.services.calc.core import MiningResult
from app.services.calc.service import run_calc, run_compare
from app.services.market.service import MarketUnavailableError

router = APIRouter(prefix="/calc", tags=["calc"])

# Optional auth: the compare endpoint is open to everyone, but a valid PRO token
# unlocks the economy delta. auto_error=False so anonymous calls still go through.
_optional_bearer = HTTPBearer(auto_error=False)


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> models.User | None:
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    if payload is None or "user_id" not in payload:
        return None
    return crud_users.get_user(db, user_id=int(payload["user_id"]))


def _calc_response(result: MiningResult, req: CalcRequest) -> CalcResponse:
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
        input=req,
    )


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

    response = _calc_response(result, req)
    response.market_captured_at = captured_at.isoformat()
    return response


@router.post("/compare", response_model=CompareResponse)
def compare_profitability(
    req: CompareRequest,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_optional_user),
) -> CompareResponse:
    """Stock-vs-custom economy comparison.

    Open to everyone. The full delta (and the custom-side result) is a PRO
    feature: non-PRO callers receive the stock result plus ``pro_required=true``
    and the delta withheld, so the UI can show 🔒 and softly invite an upgrade.
    """
    try:
        result, captured_at = run_compare(db, req)
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

    base_req = CalcRequest(
        hashrate_ths=req.hashrate_ths,
        power_w=req.power_w,
        quantity=req.quantity,
        power_price=req.power_price,
        pool_fee_pct=req.pool_fee_pct,
        uptime_pct=req.uptime_pct,
        hardware_cost=req.hardware_cost,
        premium=req.premium,
    )
    base_response = _calc_response(result.base, base_req)

    is_pro = bool(current_user and current_user.is_pro)
    if not is_pro:
        return CompareResponse(
            base=base_response,
            custom=None,
            delta=CompareDelta(pro_required=True),
            market_captured_at=captured_at.isoformat(),
        )

    # The custom MiningResult was computed from the resolved custom specs; the
    # actual custom hashrate/power are reflected in the delta fields.
    custom_response = _calc_response(result.custom, base_req)
    return CompareResponse(
        base=base_response,
        custom=custom_response,
        delta=CompareDelta(
            delta_profit_day=result.delta_profit_day,
            delta_power_w=result.delta_power_w,
            delta_power_cost_day=result.delta_power_cost_day,
            delta_hashrate=result.delta_hashrate,
            delta_efficiency_j_per_th=result.delta_efficiency_j_per_th,
            economy_note=result.economy_note,
            pro_required=False,
        ),
        market_captured_at=captured_at.isoformat(),
    )
