import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.calc import _calc_response
from app.core.config import get_settings
from app.crud import calc as crud_calc
from app.crud import firmware as crud_firmware
from app.crud import users as crud_users
from app.db.session import get_db
from app.schemas.calc import (
    CalcRequest,
    FunnelMeta,
    InternalCalcRequest,
    InternalCalcResponse,
    InternalCalcStatus,
    PowerPriceSaveRequest,
)
from app.services.calc import funnel
from app.services.calc.service import resolve_model_specs, run_calc
from app.services.market.service import MarketUnavailableError, refresh_market_data

router = APIRouter(prefix="/internal", tags=["internal"])

settings = get_settings()


def _require_bot_secret(x_bot_secret: str | None) -> None:
    expected = settings.bot_internal_secret
    if (
        not expected
        or not x_bot_secret
        or not secrets.compare_digest(x_bot_secret, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bot secret"
        )


def _funnel_meta(state: funnel.FunnelState) -> FunnelMeta:
    return FunnelMeta(
        is_pro=state.is_pro,
        stage=state.stage.value,
        calc_index=state.calc_index,
        intro_left=state.intro_left,
        daily_left=state.daily_left,
        intro_spent=state.intro_spent,
        pro_hint=state.pro_hint,
    )


@router.post("/refresh-market")
def refresh_market(
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> dict:
    """Force-refresh the market snapshot. Intended for a scheduler/cron or the
    bot to call periodically so the calc core never blocks on a cold cache."""
    _require_bot_secret(x_bot_secret)
    try:
        data, captured_at = refresh_market_data(db)
    except MarketUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "status": "ok",
        "captured_at": captured_at.isoformat(),
        "btc_price_usdt": str(data.btc_price_usdt),
        "network_difficulty": str(data.network_difficulty),
        "block_reward_btc": str(data.block_reward_btc),
    }


@router.get("/calc/status", response_model=InternalCalcStatus)
def calc_status(
    telegram_user_id: int,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> InternalCalcStatus:
    """Funnel snapshot for the user's *next* calculation + their saved price.

    Performs no calculation and records nothing; the bot uses it to show the
    progress line and offer the saved power price before the flow starts."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=telegram_user_id
    )
    settings_row = crud_users.get_or_create_settings(db, user_id=user.id)
    state = funnel.evaluate(
        is_pro=bool(user.is_pro),
        total_runs=crud_calc.count_runs(db, user_id=user.id),
        runs_today=crud_calc.count_runs_today(db, user_id=user.id),
    )
    return InternalCalcStatus(
        funnel=_funnel_meta(state),
        default_power_price=settings_row.default_power_price,
        currency=settings_row.currency or "USDT",
    )


@router.post("/calc", response_model=InternalCalcResponse)
def internal_calc(
    req: InternalCalcRequest,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> InternalCalcResponse:
    """Run a profitability calculation on behalf of a Telegram user.

    Resolves PRO status + funnel position server-side so the bot never
    duplicates the rules. When a FREE user has exhausted both the intro pool and
    today's quota, no calc is run: ``allowed=False`` and the funnel meta carries
    the paywall invite."""
    _require_bot_secret(x_bot_secret)

    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=req.telegram_user_id
    )

    state = funnel.evaluate(
        is_pro=bool(user.is_pro),
        total_runs=crud_calc.count_runs(db, user_id=user.id),
        runs_today=crud_calc.count_runs_today(db, user_id=user.id),
    )
    if not state.allowed:
        return InternalCalcResponse(
            allowed=False, funnel=_funnel_meta(state), result=None
        )

    # Resolve specs: catalog model wins over manual input.
    has_firmware = False
    if req.device_model_id is not None:
        try:
            hashrate_ths, power_w = resolve_model_specs(db, req.device_model_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        has_firmware = bool(
            crud_firmware.list_presets(
                db, device_model_id=req.device_model_id, limit=1
            )
        )
    else:
        hashrate_ths, power_w = req.hashrate_ths, int(req.power_w)

    # PRO relaxes the strict free-mode bounds (qty cap etc).
    try:
        calc_req = CalcRequest(
            hashrate_ths=hashrate_ths,
            power_w=power_w,
            quantity=req.quantity,
            power_price=req.power_price,
            pool_fee_pct=req.pool_fee_pct,
            uptime_pct=req.uptime_pct,
            hardware_cost=req.hardware_cost,
            premium=bool(user.is_pro),
        )
        result, captured_at = run_calc(db, calc_req)
    except MarketUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Record the run only after a successful calc, so a rejected/invalid request
    # never consumes a funnel slot.
    crud_calc.record_run(
        db,
        user_id=user.id,
        device_model_id=req.device_model_id,
        hashrate_ths=hashrate_ths,
        power_w=power_w,
        quantity=req.quantity,
        power_price=req.power_price,
        currency=req.currency,
        net_profit_day_usdt=result.net_profit_day,
    )

    if req.save_power_price:
        crud_users.set_default_power_price(
            db, user_id=user.id, power_price=req.power_price, currency=req.currency
        )

    calc_response = _calc_response(result, calc_req)
    calc_response.market_captured_at = captured_at.isoformat()

    return InternalCalcResponse(
        allowed=True,
        funnel=_funnel_meta(state),
        result=calc_response,
        has_firmware=has_firmware,
        device_model_id=req.device_model_id,
    )


@router.post("/settings/power-price")
def save_power_price(
    req: PowerPriceSaveRequest,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> dict:
    """Persist a user's default price per kWh (available to FREE and PRO)."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=req.telegram_user_id
    )
    row = crud_users.set_default_power_price(
        db, user_id=user.id, power_price=req.power_price, currency=req.currency
    )
    return {
        "status": "ok",
        "default_power_price": str(row.default_power_price),
        "currency": row.currency,
    }
