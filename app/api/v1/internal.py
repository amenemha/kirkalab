import secrets
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
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
    CalcLocalView,
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
from app.schemas.fx import (
    CurrencyOut,
    FxConvertRequest,
    FxConvertResponse,
    FxRatesResponse,
)
from app.services.billing import service as billing_service
from app.services.billing.entitlement import is_pro as entitlement_is_pro
from app.services.billing.entitlement import reconcile_user
from app.services.calc import funnel
from app.services.calc.export import (
    CalcExportData,
    build_calc_workbook,
    export_filename,
)
from app.services.calc.service import resolve_model_algo, resolve_model_specs, run_calc, run_calc_ex  # noqa: F401
from app.crud import fx as crud_fx
from app.services.fx import service as fx_service
from app.services.fx.service import RateUnavailableError
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


def _local_view(db: Session, result, currency: str) -> CalcLocalView | None:
    """Build the local-currency presentation layer over a USDT result.

    Additive only: returns None for USDT (the bot shows the USDT result as-is) or
    when no FX rate can be resolved (graceful degradation — bot falls back to
    USDT). The USDT headline figures are converted at the current anchor rate and
    rounded to the target currency's display ``decimals``."""
    code = (currency or "USDT").upper()
    cur = crud_fx.get_currency(db, code)
    if cur is None or not cur.is_active or not cur.is_fiat or code == "USDT":
        return None
    try:
        rate = fx_service.get_anchor_rate(db, code)
    except RateUnavailableError:
        return None

    def conv(amount: Decimal) -> Decimal:
        return fx_service.convert(db, amount, from_code="USDT", to_code=code)

    return CalcLocalView(
        currency=code,
        symbol=cur.symbol,
        rate=rate,
        net_profit_day=conv(result.net_profit_day),
        net_profit_month=conv(result.net_profit_month),
        net_profit_year=conv(result.net_profit_year),
        power_cost_day=conv(result.power_cost_day),
        stale=False,
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


@router.get("/fx/rates", response_model=FxRatesResponse)
def fx_rates(
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> FxRatesResponse:
    """Current anchor rates (1 USDT = ? fiat) plus the currency catalog.

    Rates are served from the Redis cache when warm, refreshed from CoinGecko on
    a miss, and fall back to the latest persisted ``fx_rates`` snapshot when the
    source is down (``stale=true``). The bot uses this to render the currency
    picker and to convert results client-side when it prefers to."""
    _require_bot_secret(x_bot_secret)
    currencies = crud_fx.list_currencies(db, active_only=True)
    try:
        rates = fx_service.get_anchor_rates(db)
        stale = False
    except RateUnavailableError:
        # No fresh and no persisted rates at all: return an empty map rather than
        # erroring, so the bot can still show the catalog and fall back to USDT.
        rates = {}
        stale = True
    return FxRatesResponse(
        base="USDT",
        rates=rates,
        currencies=[CurrencyOut.model_validate(c) for c in currencies],
        stale=stale,
    )


@router.post("/fx/convert", response_model=FxConvertResponse)
def fx_convert(
    req: FxConvertRequest,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> FxConvertResponse:
    """Convert a Decimal amount between two currencies at the current rate.

    Unknown/inactive target currency or a missing rate yields 422 so the caller
    can fall back to USDT. The math is Decimal end-to-end and rounded to the
    target currency's configured ``decimals``."""
    _require_bot_secret(x_bot_secret)
    to_cur = crud_fx.get_currency(db, req.to_currency.upper())
    if to_cur is None or not to_cur.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown currency: {req.to_currency}",
        )
    from_cur = crud_fx.get_currency(db, req.from_currency.upper())
    if from_cur is None or not from_cur.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown currency: {req.from_currency}",
        )
    try:
        converted = fx_service.convert(
            db,
            req.amount,
            from_code=req.from_currency,
            to_code=req.to_currency,
        )
    except RateUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    # Echo the effective rate (1 from = rate to) for transparency.
    rate = fx_service.convert(
        db, Decimal(1), from_code=req.from_currency, to_code=req.to_currency
    )
    return FxConvertResponse(
        amount=req.amount,
        from_currency=req.from_currency.upper(),
        to_currency=req.to_currency.upper(),
        converted=converted,
        rate=rate,
    )


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


XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@router.get("/calc/{run_id}/export.xlsx")
def export_calc_xlsx(
    run_id: int,
    telegram_user_id: int,
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> Response:
    """Export one saved calculation as an .xlsx document (Queue 2.2, PRO-only).

    Gating + scoping mirror the history detail endpoint:

    * Ownership/retention: the run must belong to ``telegram_user_id`` and still
      be inside the retention window, otherwise 404 — a user can never export
      someone else's (or an expired) calculation.
    * PRO gate: Excel export is a PRO feature. A FREE user (resolved with the
      same lazy-expiration entitlement as everywhere else) gets 403 so the bot
      can show the soft upsell. The ownership check runs first so a FREE user
      probing other people's ids still only ever sees 404.

    The body is the workbook bytes with a ``Content-Disposition`` attachment so
    Telegram's ``sendDocument`` (or a browser) downloads it with a sane name."""
    _require_bot_secret(x_bot_secret)
    user = crud_users.get_or_create_telegram_user(
        db, telegram_user_id=telegram_user_id
    )
    user = reconcile_user(db, user)
    user_is_pro = entitlement_is_pro(user)

    cutoff = crud_calc.history_cutoff(
        retention_days=_retention_days_for(user_is_pro)
    )
    run = crud_calc.get_history_run(
        db, user_id=user.id, run_id=run_id, cutoff=cutoff
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="calculation not found or expired",
        )

    if not user_is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Excel export is a PRO feature",
        )

    payload = build_calc_workbook(
        CalcExportData(
            run_id=run.id,
            device_name=run.device_name or "Оборудование",
            quantity=run.quantity,
            currency=run.currency or "USDT",
            created_at=run.created_at,
            hashrate_ths=run.hashrate_ths,
            power_w=run.power_w,
            power_price=run.power_price,
            net_profit_day=run.net_profit_day_usdt,
            net_profit_month=run.net_profit_month_usdt,
        )
    )
    filename = export_filename(run.id, run.created_at)
    # RFC 5987 filename* keeps the ASCII-safe name while allowing UTF-8 clients.
    disposition = (
        f"attachment; filename=\"{filename}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )


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
    algorithm: str | None = None
    hashrate_unit: str | None = None
    power_price_usdt = Decimal(str(req.power_price))
    ppc = (req.power_price_currency or "USDT").upper()
    if ppc not in ("USDT", "USD"):
        try:
            rate = fx_service.get_anchor_rate(db, ppc)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"нет курса {ppc}") from exc
        power_price_usdt = (power_price_usdt / Decimal(str(rate))).quantize(Decimal("0.000001"))
    has_firmware = False
    device_name = req.device_name
    if req.device_model_id is not None:
        try:
            hashrate_ths, power_w = resolve_model_specs(db, req.device_model_id)
            algorithm, hashrate_unit = resolve_model_algo(db, req.device_model_id)
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
            power_price=power_price_usdt,
            pool_fee_pct=req.pool_fee_pct,
            uptime_pct=req.uptime_pct,
            hardware_cost=req.hardware_cost,
            premium=user_is_pro,
            algorithm=algorithm,
            hashrate_unit=hashrate_unit,
        )
        result, captured_at, coins = run_calc_ex(db, calc_req)
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
    run = crud_calc.record_run(
        db,
        user_id=user.id,
        device_model_id=req.device_model_id,
        device_name=device_name,
        hashrate_ths=hashrate_ths,
        power_w=power_w,
        quantity=req.quantity,
        power_price=power_price_usdt,
        currency="USDT",
        net_profit_day_usdt=result.net_profit_day,
        net_profit_month_usdt=result.net_profit_month,
    )

    if req.save_power_price:
        crud_users.set_default_power_price(
            db, user_id=user.id, power_price=req.power_price, currency=ppc
        )

    calc_response = _calc_response(result, calc_req)
    calc_response.market_captured_at = captured_at.isoformat()
    calc_response.algorithm = algorithm
    calc_response.coins = coins

    local = _local_view(db, result, req.currency)

    return InternalCalcResponse(
        allowed=True,
        funnel=_funnel_meta(state),
        result=calc_response,
        has_firmware=has_firmware,
        device_model_id=req.device_model_id,
        run_id=run.id,
        local=local,
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


@router.get("/farm/summary")
def internal_farm_summary(
    telegram_user_id: int,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Compact farm summary for the bot (admins only)."""
    _require_bot_secret(x_bot_secret)
    from app.api.v1.farm import errors as farm_errors, summary as farm_summary
    from app.crud.users import get_user_by_telegram_id

    user = get_user_by_telegram_id(db, telegram_user_id=telegram_user_id)
    owners = set(settings.owner_telegram_ids or [])
    if not ((user and user.is_admin) or telegram_user_id in owners):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Farm is not available")
    s = farm_summary(db)
    e = farm_errors(None, None, None, None, None, None, None, "code", db)
    s["top_errors"] = e["groups"][:6]
    s.pop("clients", None)
    return s


@router.get("/pools/summary")
def internal_pools_summary(
    telegram_user_id: int,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Pool totals for the bot: now per coin, yesterday per coin, drops and broken links (admins only)."""
    _require_bot_secret(x_bot_secret)
    from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

    from app.api.v1.pools import accounts as pool_accounts, all_daily
    from app.crud.users import get_user_by_telegram_id

    user = get_user_by_telegram_id(db, telegram_user_id=telegram_user_id)
    owners = set(settings.owner_telegram_ids or [])
    if not ((user and user.is_admin) or telegram_user_id in owners):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Pools are not available")
    items = pool_accounts(db)["items"]

    def _pl(a: dict) -> str:
        return (a["account"] or a["user"] or "—") + ("" if a["provider"] == "viabtc" else f" · {a['pool']}")

    coins: dict[str, dict] = {}
    drops, broken, last = [], [], None
    for a in items:
        if not a["active"]:
            continue
        c = coins.setdefault(a["coin"] or "?", {"coin": a["coin"] or "?", "ths_1h": 0.0, "ths_1d": 0.0, "w_active": 0, "w_inactive": 0, "links": 0, "yesterday": 0.0})
        c["links"] += 1
        c["ths_1h"] += a["ths_1h"] or 0
        c["ths_1d"] += a["ths_1d"] or 0
        c["w_active"] += a["w_active"] or 0
        c["w_inactive"] += a["w_inactive"] or 0
        if a["error"]:
            broken.append({"account": _pl(a), "group": a["group"], "error": a["error"][:80]})
        elif (a["ths_1d"] or 0) > 0 and (a["ths_1h"] or 0) < a["ths_1d"] * 0.7:
            drops.append({"account": _pl(a), "group": a["group"], "coin": a["coin"], "ths_1h": a["ths_1h"], "ths_1d": a["ths_1d"]})
        if a["last_ok"] and (last is None or a["last_ok"] > last):
            last = a["last_ok"]
    y = (_dt.now(_tz(_td(hours=3))).date() - _td(days=1)).isoformat()
    for it in all_daily(y, y, 3, None, db)["items"]:
        d = it["days"][0] if it["days"] else None
        if d and d["ths"] is not None:
            coins[it["account"]["coin"] or "?"]["yesterday"] += d["ths"]
    drops.sort(key=lambda r: (r["ths_1h"] or 0) - r["ths_1d"])
    _ = _date
    return {"coins": sorted(coins.values(), key=lambda c: -c["ths_1d"]), "drops": drops[:8], "broken": broken[:8],
            "broken_total": len(broken), "drops_total": len(drops), "yesterday": y, "updated": last}


# --------------------------------------------------------------------------- #
# Уведомления фермы — экран настроек в боте.
# --------------------------------------------------------------------------- #
def _farm_access(db: Session, telegram_user_id: int) -> None:
    from app.crud.users import get_user_by_telegram_id

    user = get_user_by_telegram_id(db, telegram_user_id=telegram_user_id)
    owners = set(settings.owner_telegram_ids or [])
    if not ((user and user.is_admin) or telegram_user_id in owners):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Уведомления фермы недоступны")


@router.get("/notify")
def internal_notify_get(
    telegram_user_id: int,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    _require_bot_secret(x_bot_secret)
    _farm_access(db, telegram_user_id)
    from app.services.notify import service as ns

    return {"prefs": ns.get_prefs(db, telegram_user_id), "types": ns.TYPES, "levels": ns.LEVEL_NAMES}


@router.put("/notify")
def internal_notify_put(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    _require_bot_secret(x_bot_secret)
    tg = int(body.get("telegram_user_id") or 0)
    _farm_access(db, tg)
    from app.services.notify import service as ns

    try:
        prefs = ns.set_prefs(db, tg, body.get("values") or {})
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"prefs": prefs, "types": ns.TYPES, "levels": ns.LEVEL_NAMES}


@router.post("/notify/test")
def internal_notify_test(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    _require_bot_secret(x_bot_secret)
    tg = int(body.get("telegram_user_id") or 0)
    _farm_access(db, tg)
    from app.services.notify import service as ns

    return {"sent": ns.send_test(db, tg)}


@router.post("/activity")
def internal_activity(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Бот сообщает о нажатой кнопке/команде — для админки."""
    _require_bot_secret(x_bot_secret)
    from app.services import activity as act

    tg = int(body.get("telegram_user_id") or 0) or None
    action = str(body.get("action") or "")[:120]
    if not tg or not action:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="telegram_user_id и action обязательны")
    meta = {k: body.get(k) for k in ("username", "name") if body.get(k)}
    act.record(db, source="bot", action=action, tg_id=tg, label=act.bot_label(action), meta=meta)
    return {"ok": True}


@router.post("/support")
def internal_support(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Человек написал менеджеру из бота."""
    _require_bot_secret(x_bot_secret)
    from app.services import support as sup

    tg = int(body.get("telegram_user_id") or 0)
    text = str(body.get("text") or "").strip()
    file = body.get("file") if isinstance(body.get("file"), dict) and body["file"].get("file_id") else None
    if not tg or not (text or file):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="telegram_user_id и text/file обязательны")
    meta = {k: body.get(k) for k in ("username", "name") if body.get(k)}
    if body.get("quote"):
        meta["quote"] = str(body["quote"])[:1500]
        meta["quote_bot"] = bool(body.get("quote_bot"))
        ql = str(body.get("quote_link") or "")
        if ql.startswith("https://kirkalab.ru/"):
            meta["quote_link"] = ql[:300]
    m = sup.incoming(db, tg, text, meta, file=file)
    return {"ok": True, "id": m.id, "managers": len((m.meta or {}).get("fwd") or {})}


@router.post("/support/reply")
def internal_support_reply(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Менеджер ответил в Telegram на пересланное сообщение — передаём человеку."""
    _require_bot_secret(x_bot_secret)
    from app.services import support as sup

    mgr = int(body.get("manager_tg") or 0)
    rid = int(body.get("reply_to_message_id") or 0)
    text = str(body.get("text") or "").strip()
    if mgr not in sup.managers(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not a manager")
    file = body.get("file") if isinstance(body.get("file"), dict) and body["file"].get("file_id") else None
    src = sup.find_by_forward(db, mgr, rid)
    if not src or not (text or file):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not a support thread")
    by = db.scalar(select(models.User.id).where(models.User.telegram_user_id == mgr))
    r = sup.send_out(db, [src.tg_id], text, by_user_id=by, file=file)
    return {"ok": bool(r["sent"]), "to": src.tg_id}


def _conn_user(db: Session, tg: int) -> models.User:
    u = db.scalar(select(models.User).where(models.User.telegram_user_id == tg))
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нажмите /start")
    return u


@router.post("/connections")
def internal_connections(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Бот: подключения человека. action: list | croft | pool | recognize."""
    _require_bot_secret(x_bot_secret)
    from app.services import connections as cn

    act = str(body.get("action") or "list")
    if act == "recognize":
        r = cn.recognize(str(body.get("text") or ""))
        return {"pool": r}
    u = _conn_user(db, int(body.get("tg_id") or 0))
    try:
        if act == "croft":
            a = cn.croft_connect(db, u, str(body.get("login") or ""), str(body.get("password") or ""))
            cn.notify_managers(db, u, f"подключил CROFT ({a.login}) — {cn.croft_text(a.stats or {})}")
            return {"ok": True, "croft": cn.croft_view(a), "text": cn.croft_text(a.stats or {})}
        if act == "pool":
            if not cn.recognize(str(body.get("text") or "")):
                return {"ok": False, "unknown": True, "error": "Не узнала ссылку пула"}
            r = cn.pool_connect(db, u, str(body.get("text") or ""))
            if not r.get("already"):
                cn.notify_managers(db, u, f"добавил ссылку наблюдателя {r['name']}" + (f" {r['coin']}" if r.get("coin") else ""))
            return {"ok": True, **r}
    except cn.ConnError as e:
        return {"ok": False, "error": e.text}
    out = cn.of_user(db, u)
    out["is_admin"] = u.is_admin
    return out


@router.post("/wallets")
def internal_wallets(
    body: dict,
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
    db: Session = Depends(get_db),
):
    """Бот: кошельки человека. action: detect | list | add | mute | unmute | delete."""
    _require_bot_secret(x_bot_secret)
    from app.db.wallet_models import WalletWatch
    from app.services.wallets import watch as ww
    from app.services.wallets.chains import CHAINS, find_address

    act = str(body.get("action") or "list")
    if act == "detect":
        f = find_address(str(body.get("text") or ""))
        return {"address": f[0], "chains": [{"id": c, "name": CHAINS[c]["name"], "net": CHAINS[c]["net"]} for c in f[1]]} if f else {"address": None}
    u = _conn_user(db, int(body.get("tg_id") or 0))
    if act == "add":
        try:
            ws = ww.add(db, u, str(body.get("address") or ""), body.get("label"), body.get("chains") or None)
        except ww.WalletError as e:
            return {"ok": False, "error": e.text}
        return {"ok": True, "added": [ww.view(w, db) for w in ws]}
    if act in ("mute", "unmute", "delete"):
        w = ww.owned(db, u, int(body.get("id") or 0))
        if not w:
            return {"ok": False, "error": "Кошелёк не найден"}
        if act == "delete":
            db.delete(w)
        else:
            w.muted = act == "mute"
        db.commit()
        return {"ok": True}
    rows = db.scalars(select(WalletWatch).where(WalletWatch.user_id == u.id).order_by(WalletWatch.id)).all()
    out = {"items": [ww.view(w, db) for w in rows], "limit": ww.limit_of(u)}
    for it in out["items"]:
        last = ww.history(db, db.get(WalletWatch, it["id"]), limit=1)
        it["last"] = last[0] if last else None
    return out
