import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.calc import _calc_response
from app.core.config import get_settings
from app.crud import calc as crud_calc
from app.crud import firmware as crud_firmware
from app.crud import users as crud_users
from app.db import models
from app.db.session import get_db
from app.schemas.billing import (
    BillingActivateRequest,
    PlanOut,
    PlansResponse,
    SubscriptionState,
)
from app.schemas.calc import (
    CalcRequest,
    FunnelMeta,
    HistoryPage,
    HistoryRunOut,
    InternalCalcRequest,
    InternalCalcResponse,
    InternalCalcStatus,
    InternalProfile,
    PowerPriceSaveRequest,
)
from app.services.billing import service as billing_service
from app.services.billing.entitlement import is_pro as entitlement_is_pro
from app.services.billing.entitlement import reconcile_user
from app.services.calc import funnel
from app.services.calc.service import resolve_model_specs, run_calc
from app.services.market.service import MarketUnavailableError, refresh_market_data

router = APIRouter(prefix="/internal", tags=["internal"])

settings = get_settings()

# History rows per page. Matches the bot's ``history_format.PAGE_SIZE`` so the
# inline keyboard (one open-button per item) stays well under Telegram limits.
HISTORY_PAGE_SIZE = 5


def _history_out(run: models.CalculationRun) -> HistoryRunOut:
    """Serialize a CalculationRun row into the history payload."""
    return HistoryRunOut(
        id=run.id,
        device_name=run.device_name,
        device_model_id=run.device_model_id,
        quantity=run.quantity,
        currency=run.currency,
        hashrate_ths=run.hashrate_ths,
        power_w=run.power_w,
        power_price=run.power_price,
        net_profit_day_usdt=run.net_profit_day_usdt,
        net_profit_month_usdt=run.net_profit_month_usdt,
        created_at=run.created_at.isoformat() if run.created_at else "",
    )


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


def _model_display_name(db: Session, device_model_id: int) -> str | None:
    """Build a human-readable catalog name ("Brand Model Variant") for history."""
    model = db.get(models.DeviceModel, device_model_id)
    if model is None:
        return None
    parts = [model.brand, model.model_name]
    if model.variant:
        parts.append(model.variant)
    name = " ".join(p for p in parts if p).strip()
    return name[:128] or None


def _funnel_meta(state: funnel.FunnelState) -> FunnelMeta:
    return FunnelMeta(
        is_pro=state.is_pro,
        stage=state.stage.value,
        calc_index=state.calc_index,
        intro_left=state.intro_left,
        daily_left=state.daily_left,
        intro_spent=state.intro_spent,
        pro_hint=state.pro_hint,
        intro_calcs=state.intro_calcs,
        daily_limit=state.daily_limit,
    )


def _evaluate_funnel(
    *, is_pro: bool, total_runs: int, runs_today: int
) -> funnel.FunnelState:
    """Evaluate the FREE funnel with the configured (not hardcoded) limits."""
    return funnel.evaluate(
        is_pro=is_pro,
        total_runs=total_runs,
        runs_today=runs_today,
        intro_calcs=settings.free_intro_calcs,
        daily_limit=settings.free_calcs_per_day,
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
    user = reconcile_user(db, user)
    settings_row = crud_users.get_or_create_settings(db, user_id=user.id)
    state = _evaluate_funnel(
        is_pro=entitlement_is_pro(user),
        total_runs=crud_calc.count_runs(db, user_id=user.id),
        runs_today=crud_calc.count_runs_today(db, user_id=user.id),
    )
    return InternalCalcStatus(
        funnel=_funnel_meta(state),
        default_power_price=settings_row.default_power_price,
        currency=settings_row.currency or "USDT",
    )


def _retention_days_for(is_pro: bool) -> int:
    """Active history retention window in days for this user (0 = unlimited).

    PRO has unbounded history; FREE uses the configured window. Centralised so
    the list and detail endpoints agree on the cutoff."""
    if is_pro:
        return 0
    return settings.free_history_retention_days


@router.get("/history", response_model=HistoryPage)
def calc_history(
    telegram_user_id: int,
    page: int = 0,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> HistoryPage:
    """A page of the user's saved calculations, newest first (Queue 2.3).

    Retention is enforced at the query level: on FREE only the last
    ``free_history_retention_days`` are returned; PRO sees everything. Nothing is
    deleted — older rows are simply filtered out of the view. ``truncated``
    reports whether older rows were hidden so the bot can surface the soft PRO
    hint."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=telegram_user_id
    )
    user = reconcile_user(db, user)
    user_is_pro = entitlement_is_pro(user)

    retention_days = _retention_days_for(user_is_pro)
    cutoff = crud_calc.history_cutoff(retention_days=retention_days)

    page_size = HISTORY_PAGE_SIZE
    visible_total = crud_calc.count_history(db, user_id=user.id, cutoff=cutoff)
    page = max(page, 0)
    last_page = max((visible_total - 1) // page_size, 0) if visible_total else 0
    page = min(page, last_page)

    rows = crud_calc.list_history(
        db,
        user_id=user.id,
        cutoff=cutoff,
        offset=page * page_size,
        limit=page_size,
    )

    truncated = False
    if cutoff is not None:
        overall_total = crud_calc.count_history(db, user_id=user.id, cutoff=None)
        truncated = overall_total > visible_total

    return HistoryPage(
        items=[_history_out(r) for r in rows],
        total=visible_total,
        page=page,
        page_size=page_size,
        is_pro=user_is_pro,
        truncated=truncated,
        retention_days=retention_days,
    )


@router.get("/history/{run_id}", response_model=HistoryRunOut)
def calc_history_detail(
    run_id: int,
    telegram_user_id: int,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> HistoryRunOut:
    """One saved calculation for the detail screen, scoped to the user.

    Honours the same retention window as the list: a run that has fallen outside
    the FREE window cannot be opened (404), matching what the list shows."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=telegram_user_id
    )
    user = reconcile_user(db, user)
    cutoff = crud_calc.history_cutoff(
        retention_days=_retention_days_for(entitlement_is_pro(user))
    )
    run = crud_calc.get_history_run(
        db, user_id=user.id, run_id=run_id, cutoff=cutoff
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="calculation not found or expired",
        )
    return _history_out(run)


@router.get("/profile", response_model=InternalProfile)
def internal_profile(
    telegram_user_id: int,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> InternalProfile:
    """Base cabinet for a Telegram user, creating it on first access.

    FREE auth is automatic: the user is found/created by telegram id, with no
    email login. ``is_linked`` reports whether the cabinet has been tied to a
    real email account (PRO/web-app), as opposed to the auto placeholder."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=telegram_user_id
    )
    user = reconcile_user(db, user)
    placeholder_email = f"tg_{telegram_user_id}@telegram.bot"
    return InternalProfile(
        id=user.id,
        handle=user.handle,
        is_pro=entitlement_is_pro(user),
        is_linked=user.email != placeholder_email,
        created_at=user.created_at.isoformat() if user.created_at else "",
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
    user = reconcile_user(db, user)
    user_is_pro = entitlement_is_pro(user)

    state = _evaluate_funnel(
        is_pro=user_is_pro,
        total_runs=crud_calc.count_runs(db, user_id=user.id),
        runs_today=crud_calc.count_runs_today(db, user_id=user.id),
    )
    if not state.allowed:
        return InternalCalcResponse(
            allowed=False, funnel=_funnel_meta(state), result=None
        )

    # Resolve specs: catalog model wins over manual input.
    has_firmware = False
    device_name = req.device_name
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
        if not device_name:
            device_name = _model_display_name(db, req.device_model_id)
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
            premium=user_is_pro,
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
        device_name=device_name,
        hashrate_ths=hashrate_ths,
        power_w=power_w,
        quantity=req.quantity,
        power_price=req.power_price,
        currency=req.currency,
        net_profit_day_usdt=result.net_profit_day,
        net_profit_month_usdt=result.net_profit_month,
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


# --------------------------------------------------------------------------- #
# Billing (Telegram Stars PRO).
# --------------------------------------------------------------------------- #
@router.get("/plans", response_model=PlansResponse)
def list_plans(
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> PlansResponse:
    """Active plans for the bot's PRO screen. Prices come from the table."""
    _require_bot_secret(x_bot_secret)
    plans = billing_service.get_active_plans(db)
    return PlansResponse(plans=[PlanOut.model_validate(p) for p in plans])


@router.post("/billing/activate", response_model=SubscriptionState)
def billing_activate(
    req: BillingActivateRequest,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> SubscriptionState:
    """Apply a completed Telegram Stars payment: create/renew the subscription
    and set the user's PRO entitlement. Idempotent on the charge id."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=req.telegram_id
    )

    # Detect an idempotent repeat so we can report it to the bot without
    # re-extending the period.
    already = (
        db.scalar(
            select(models.Subscription.id).where(
                models.Subscription.telegram_payment_charge_id
                == req.telegram_payment_charge_id
            )
        )
        is not None
    )

    try:
        sub = billing_service.activate_subscription(
            db,
            user=user,
            plan_code=req.plan_code,
            telegram_payment_charge_id=req.telegram_payment_charge_id,
            total_amount=req.total_amount,
        )
    except billing_service.BillingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    db.refresh(user)
    return SubscriptionState(
        is_pro=entitlement_is_pro(user),
        plan_code=sub.plan_code,
        status=sub.status,
        started_at=sub.started_at,
        expires_at=sub.expires_at,
        premium_until=user.premium_until,
        already_applied=already,
    )
