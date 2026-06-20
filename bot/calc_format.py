"""Pure formatting for the profitability *result* screen (core of the product).

Free of aiogram/Telegram imports so the whole result card can be unit-tested in
the backend test environment (no aiogram needed), mirroring
``bot.catalog_format``.

The screen renders the FREE currency funnel by ``stage`` (from the backend
``funnel`` meta):

* ``local_full``    (intro 1–3): income in local currency + USDT; payback/ROI/
                    break-even USDT-only with a 🔒 invite; local shown in full.
* ``local_blurred`` (intro 4–5): income USDT-only; local-currency figures
                    blurred ("▒▒▒ ₽ 🔒 в PRO"); payback USDT-only.
* ``usdt_only``     (intro spent): everything USDT-only, permanent soft invite.
* ``pro``           PRO: everything in the chosen currency, no gating.

The warm tone is intentional — we invite, never scold or block with modals.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# Display symbols for the local currencies we know about. USDT keeps its ticker.
_CURRENCY_SYMBOL = {
    "USDT": "USDT",
    "USD": "$",
    "RUB": "₽",
    "CNY": "¥",
    "EUR": "€",
    "KZT": "₸",
}

_BLUR = "▒▒▒"


def _sym(currency: str) -> str:
    return _CURRENCY_SYMBOL.get((currency or "USDT").upper(), currency)


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(value: Any, currency: str = "USDT", places: int = 2) -> str:
    """Format a money amount with thousands separators (e.g. 1 234.56 USDT)."""
    d = _dec(value)
    if d is None:
        return "—"
    q = Decimal(10) ** -places
    d = d.quantize(q)
    whole, _, frac = f"{d:.{places}f}".partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    grouped = f"{int(whole):,}".replace(",", " ")
    body = f"{sign}{grouped}.{frac}" if places else f"{sign}{grouped}"
    return f"{body} {_sym(currency)}"


def _btc(value: Any) -> str:
    d = _dec(value)
    if d is None:
        return "—"
    trimmed = f"{d:.8f}".rstrip("0").rstrip(".") or "0"
    return f"{trimmed} BTC"


def _roi(value: Any) -> str | None:
    """Render payback period in days/months from roi_days, or None."""
    d = _dec(value)
    if d is None or d <= 0:
        return None
    days = d.quantize(Decimal("1"))
    months = (d / Decimal(30)).quantize(Decimal("0.1"))
    return f"{days} дн. (~{months} мес.)"


def _progress_line(funnel: dict) -> str | None:
    """Explicit progress: 'Расчёт N из 5' or 'Сегодня осталось N из 3'."""
    if funnel.get("is_pro"):
        return None
    if not funnel.get("intro_spent"):
        idx = funnel.get("calc_index")
        if idx is not None:
            return f"🧮 Ознакомительный расчёт {idx} из 5"
    daily_left = funnel.get("daily_left")
    if daily_left is not None:
        return f"🧮 Сегодня осталось расчётов: {daily_left} из 3"
    return None


def format_result_screen(
    *,
    result: dict,
    funnel: dict,
    title: str,
    quantity: int,
    currency: str = "USDT",
) -> str:
    """Render the full result card.

    ``result`` is the CalcResponse payload; ``funnel`` is the FunnelMeta payload.
    ``currency`` is the user's local display currency (USDT on FREE)."""
    stage = funnel.get("stage", "usdt_only")
    is_pro = bool(funnel.get("is_pro"))
    local = (currency or "USDT").upper()
    local_is_usdt = local == "USDT"

    net_day = result.get("net_profit_day")
    net_month = result.get("net_profit_month")
    net_year = result.get("net_profit_year")
    gross_day = result.get("pool_revenue_usdt_day")
    power_cost = result.get("power_cost_day")

    lines: list[str] = [f"📊 <b>{title}</b> × {quantity}", ""]

    # --- Income block (gross + net) ---
    lines.append("💰 <b>Доход (с учётом комиссии пула)</b>")
    lines.append(f"  • в день: {_money(gross_day, 'USDT')}")
    lines.append(f"  • {_btc(result.get('btc_per_day'))} в день")
    lines.append("")

    # --- Power block ---
    kwh = _power_kwh_day(result)
    lines.append("⚡ <b>Электроэнергия</b>")
    if kwh is not None:
        lines.append(f"  • {kwh} кВт·ч/день")
    lines.append(f"  • стоимость: {_money(power_cost, 'USDT')}/день")
    lines.append("")

    # --- Net profit block (funnel-aware local currency) ---
    lines.append("✅ <b>Чистая прибыль</b>")
    lines.extend(
        _profit_rows(
            net_day=net_day,
            net_month=net_month,
            net_year=net_year,
            stage=stage,
            is_pro=is_pro,
            local=local,
            local_is_usdt=local_is_usdt,
        )
    )
    lines.append("")

    # --- Payback / ROI / break-even ---
    lines.extend(
        _payback_rows(result=result, stage=stage, is_pro=is_pro)
    )

    # --- Progress + soft PRO invite ---
    progress = _progress_line(funnel)
    hint = funnel.get("pro_hint")
    if progress or hint:
        lines.append("")
    if progress:
        lines.append(progress)
    if hint:
        lines.append(hint)

    return "\n".join(lines)


def _profit_rows(
    *,
    net_day,
    net_month,
    net_year,
    stage: str,
    is_pro: bool,
    local: str,
    local_is_usdt: bool,
) -> list[str]:
    rows: list[str] = []
    # USDT figures are always shown.
    rows.append(f"  • в день: {_money(net_day, 'USDT')}")
    rows.append(f"  • в месяц: {_money(net_month, 'USDT')}")
    rows.append(f"  • в год: {_money(net_year, 'USDT')}")

    # Local currency line is only meaningful when it differs from USDT (PRO can
    # pass RUB/USD/…). On FREE the display currency is USDT, so there is nothing
    # extra to show in full/blurred stages — the blur mechanic still governs the
    # PRO upsell below.
    if is_pro or stage == "pro":
        if not local_is_usdt:
            rows.append(f"  • в день: {_money(net_day, local)} (в вашей валюте)")
        return rows

    if stage == "local_full":
        # Local fully visible (USDT == local on FREE; explicit row when distinct).
        if not local_is_usdt:
            rows.append(f"  • в день: {_money(net_day, local)}")
    elif stage == "local_blurred":
        # Local currency blurred behind the gate.
        rows.append(f"  • в вашей валюте: {_BLUR} {_sym(local)} 🔒 в PRO")
    # usdt_only: nothing extra; the standing invite is appended by the caller.
    return rows


def _payback_rows(*, result: dict, stage: str, is_pro: bool) -> list[str]:
    rows: list[str] = ["📈 <b>Окупаемость</b>"]
    roi = _roi(result.get("roi_days"))
    breakeven = result.get("break_even_power_price")

    if roi is None:
        rows.append("  • укажите стоимость оборудования для расчёта ROI")
    else:
        rows.append(f"  • окупаемость: {roi}")

    if breakeven is not None:
        rows.append(
            f"  • точка безубыточности по э/э: "
            f"{_money(breakeven, 'USDT', places=4)}/кВт·ч"
        )

    # On FREE all payback metrics are USDT-only; the local-currency version is a
    # PRO unlock. PRO already sees everything, so no extra lock line.
    if not (is_pro or stage == "pro"):
        rows.append("  🔒 в локальной валюте — в PRO")
    return rows


def _power_kwh_day(result: dict) -> str | None:
    """Derive kWh/day from cost and price when available (transparency row)."""
    cost = _dec(result.get("power_cost_day"))
    inp = result.get("input") or {}
    price = _dec(inp.get("power_price"))
    if cost is None or price is None or price <= 0:
        return None
    kwh = (cost / price).quantize(Decimal("0.01"))
    return f"{kwh}".rstrip("0").rstrip(".")


def format_limit_reached(funnel: dict) -> str:
    """Warm paywall screen shown when FREE quota is exhausted (no calc run)."""
    hint = funnel.get("pro_hint") or (
        "Бесплатные расчёты на сегодня закончились."
    )
    return (
        "💎 <b>Лимит бесплатных расчётов</b>\n\n"
        f"{hint}\n\n"
        "С PRO — безлимитные расчёты, все валюты (₽/$/¥), окупаемость и ROI "
        "без блюра. Возвращайтесь завтра или откройте PRO 🙌"
    )
