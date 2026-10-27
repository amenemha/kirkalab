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


@router.post("/model")
def calculate_by_model(body: dict, db: Session = Depends(get_db)) -> dict:
    """Расчёт по модели каталога для сайта: любой алгоритм, цена э/э в ₽ или USDT.

    Тело: device_model_id, quantity, power_price, power_price_currency (RUB|USDT),
    pool_fee_pct, uptime_pct, hardware_cost (в валюте цены), hashrate/power — переопределить."""
    from decimal import Decimal, InvalidOperation

    from app.services.calc.service import resolve_model_algo, resolve_model_specs, run_calc_ex
    from app.services.fx import service as fx_service

    def dec(v, default=None):
        if v in (None, ""):
            return default
        try:
            return Decimal(str(v).replace(",", "."))
        except InvalidOperation as exc:
            raise HTTPException(422, f"не число: {v}") from exc

    try:
        mid = int(body.get("device_model_id"))
        hr, pw = resolve_model_specs(db, mid)
        algo, unit = resolve_model_algo(db, mid)
    except (TypeError, ValueError) as exc:
        raise HTTPException(404, "модель не найдена") from exc
    hr = dec(body.get("hashrate"), hr)
    pw = int(dec(body.get("power_w"), Decimal(pw)))
    cur = str(body.get("power_price_currency") or "RUB").upper()
    rate = Decimal(1)
    if cur != "USDT":
        try:
            rate = Decimal(str(fx_service.get_anchor_rate(db, cur)))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"нет курса {cur}") from exc
    price = dec(body.get("power_price"), Decimal(0)) / rate
    hw = dec(body.get("hardware_cost"))
    try:
        req = CalcRequest(hashrate_ths=hr, power_w=pw, quantity=int(body.get("quantity") or 1),
                          power_price=price.quantize(Decimal("0.000001")),
                          pool_fee_pct=dec(body.get("pool_fee_pct"), Decimal("1.0")),
                          uptime_pct=dec(body.get("uptime_pct"), Decimal("100")),
                          hardware_cost=(hw / rate) if hw is not None else None, premium=True,
                          algorithm=algo, hashrate_unit=unit)
        result, captured_at, coins = run_calc_ex(db, req)
    except MarketUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    r = _calc_response(result, req)
    out = r.model_dump(mode="json")
    out.update(market_captured_at=captured_at.isoformat(), algorithm=algo, hashrate_unit=unit or "TH/s",
               coins=coins, currency=cur, rate=str(rate))
    if cur != "USDT":
        q = Decimal("0.01")
        out["local"] = {k: str((Decimal(str(getattr(result, k))) * rate).quantize(q)) for k in
                        ("pool_revenue_usdt_day", "power_cost_day", "net_profit_day", "net_profit_month", "net_profit_year")}
        if result.break_even_power_price is not None:
            out["local"]["break_even_power_price"] = str((result.break_even_power_price * rate).quantize(q))
    return out
