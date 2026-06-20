"""PRO billing via Telegram Stars (CALC_SPEC §4).

Lives inside Profile (the tariff never "маячит" in the main menu). Flow:

  profile:plan         -> show the plan picker (prices from the API)
  plan:buy:<code>      -> send a Stars invoice (currency XTR)
  pre_checkout_query   -> validate the plan, answer ok
  successful_payment   -> relay to /internal/billing/activate, confirm warmly

Clean chat (§3.2): the plan picker and the invoice prompt are ephemeral (the
single live screen / a removable message), but the final "PRO активирован"
confirmation is a permanent message — it is real value, so it stays in the feed.

For Telegram Stars the invoice ``currency`` is "XTR", ``provider_token`` is the
empty string, and ``LabeledPrice.amount`` is the number of stars itself (no
×100 minor-units conversion).
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot.api_client import ApiError, KirkalabApiClient
from bot.config import get_settings
from bot.keyboards import plans_kb
from bot.live_screen import edit_live_screen

logger = logging.getLogger("kirkalab.bot.billing")

router = Router()

# Invoice payload prefix: encodes the plan code so the handlers can recover it
# from pre_checkout / successful_payment without extra state.
_PAYLOAD_PREFIX = "pro:"

PLANS_INTRO = (
    "💎 <b>PRO-доступ</b>\n\n"
    "Что открывается:\n"
    "• Безлимитные расчёты\n"
    "• Все валюты (₽/$/¥) без блюра\n"
    "• Точная окупаемость и ROI\n"
    "• Сравнение прошивок и сохранение сборок\n"
    "• Смешанные фермы, стойки и контейнеры\n\n"
    "Оплата — звёздами Telegram ⭐. Выберите план:"
)


def _client(event: Message | CallbackQuery) -> KirkalabApiClient:
    return event.bot.kirkalab_client


def _secret() -> str | None:
    return get_settings().bot_internal_secret


def _plan_code_from_payload(payload: str) -> str | None:
    if payload.startswith(_PAYLOAD_PREFIX):
        return payload[len(_PAYLOAD_PREFIX):] or None
    return None


@router.callback_query(F.data == "profile:plan")
async def cb_show_plans(callback: CallbackQuery, state) -> None:
    """Render the PRO plan picker with live prices from the API."""
    secret = _secret()
    if not secret:
        await edit_live_screen(
            callback.message, state, "⚠️ Оплата временно недоступна."
        )
        await callback.answer()
        return
    try:
        plans = await _client(callback).list_plans(bot_secret=secret)
    except ApiError as exc:
        await edit_live_screen(
            callback.message, state, f"❌ Не удалось загрузить тарифы: {exc.message}"
        )
        await callback.answer()
        return

    purchasable = [p for p in plans if (p.get("price_stars") or 0) > 0]
    if not purchasable:
        await edit_live_screen(
            callback.message,
            state,
            "💎 PRO скоро будет доступен. Спасибо, что вы с нами! 🙌",
        )
        await callback.answer()
        return

    await edit_live_screen(
        callback.message, state, PLANS_INTRO, reply_markup=plans_kb(purchasable)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plan:buy:"))
async def cb_buy_plan(callback: CallbackQuery) -> None:
    """Send a Telegram Stars invoice for the chosen plan."""
    secret = _secret()
    plan_code = callback.data.rsplit(":", 1)[1]
    if not secret:
        await callback.answer("Оплата недоступна.", show_alert=True)
        return
    try:
        plans = await _client(callback).list_plans(bot_secret=secret)
    except ApiError as exc:
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return

    plan = next((p for p in plans if p.get("code") == plan_code), None)
    if plan is None or (plan.get("price_stars") or 0) <= 0:
        await callback.answer("Этот тариф недоступен.", show_alert=True)
        return

    price = int(plan["price_stars"])
    title = plan.get("title", "PRO")
    days = plan.get("period_days")
    description = (
        f"PRO-доступ Kirkalab на {days} дней. "
        "Безлимитные расчёты и все возможности PRO."
        if days
        else "PRO-доступ Kirkalab."
    )
    # Stars: currency XTR, empty provider_token, amount == number of stars.
    await callback.message.answer_invoice(
        title=title,
        description=description,
        payload=f"{_PAYLOAD_PREFIX}{plan_code}",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=price)],
        provider_token="",
    )
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    """Approve the checkout once the plan in the payload is still valid."""
    secret = _secret()
    plan_code = _plan_code_from_payload(pre_checkout.invoice_payload or "")
    if not secret or plan_code is None:
        await pre_checkout.answer(
            ok=False, error_message="Платёж не распознан. Попробуйте ещё раз."
        )
        return
    try:
        plans = await _client(pre_checkout).list_plans(bot_secret=secret)
    except ApiError:
        # Don't block a paying user on a transient API hiccup; activation is
        # idempotent and is the real gate.
        await pre_checkout.answer(ok=True)
        return
    valid = any(
        p.get("code") == plan_code and (p.get("price_stars") or 0) > 0
        for p in plans
    )
    if not valid:
        await pre_checkout.answer(
            ok=False, error_message="Тариф больше не доступен."
        )
        return
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    """Relay the completed Stars payment to the backend and confirm warmly."""
    secret = _secret()
    sp = message.successful_payment
    plan_code = _plan_code_from_payload(sp.invoice_payload or "")
    if not secret or plan_code is None:
        await message.answer(
            "✅ Оплата получена. Если PRO не подключился, напишите в поддержку."
        )
        return
    try:
        state = await _client(message).billing_activate(
            telegram_id=message.from_user.id,
            plan_code=plan_code,
            telegram_payment_charge_id=sp.telegram_payment_charge_id,
            total_amount=sp.total_amount,
            bot_secret=secret,
        )
    except ApiError as exc:
        logger.error("billing activate failed: %s", exc.message)
        await message.answer(
            "✅ Оплата получена, но активация задерживается. "
            "Мы подключим PRO в ближайшее время — спасибо за терпение!"
        )
        return

    until = _format_until(state.get("premium_until") or state.get("expires_at"))
    # Permanent confirmation message (clean-chat: this one stays in the feed).
    await message.answer(
        "💎 <b>PRO активирован!</b>\n\n"
        f"Доступ открыт до <b>{until}</b>.\n"
        "Теперь все расчёты без лимитов, любые валюты и полная окупаемость. "
        "Спасибо, что поддерживаете проект! 🙌"
    )


def _format_until(value: str | None) -> str:
    """Render an ISO datetime as DD.MM.YYYY, falling back gracefully."""
    if not value:
        return "—"
    from datetime import datetime

    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).strftime("%d.%m.%Y")
    except ValueError:
        return value[:10]
