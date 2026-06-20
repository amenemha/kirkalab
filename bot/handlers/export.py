"""Shared "📥 Экспорт в Excel" action (Queue 2.2).

Both the just-computed result screen (``calc:xlsx:<id>``) and the history detail
screen (``hist:xlsx:<id>``) offer Excel export of a saved calculation. The actual
work is identical, so it lives here once:

* call the internal export endpoint (PRO-gated + ownership-scoped server-side);
* on success send the .xlsx via ``sendDocument`` — a *new* message we keep, as
  it's the deliverable (the live screen is left untouched, clean-chat intact);
* on 403 (FREE user) edit the live screen in place into a soft PRO upsell;
* on 404 (expired/not owned) answer the callback with a friendly alert.

Kept aiogram-typed and thin; tests import it under ``pytest.importorskip``.
"""
from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from bot.api_client import ApiError, KirkalabApiClient
from bot.config import get_settings
from bot.keyboards import export_upsell_kb
from bot.live_screen import edit_live_screen, set_screen_id

EXPORT_CAPTION = "📊 Ваш расчёт доходности Kirkalab"

UPSELL_TEXT = (
    "📥 <b>Экспорт в Excel — функция PRO</b>\n\n"
    "Выгрузка расчёта в .xlsx доступна на тарифе PRO.\n"
    "Оформите PRO, чтобы скачивать аккуратные отчёты по своим расчётам, "
    "снять лимиты и открыть все валюты. 💎"
)


def _client(callback: CallbackQuery) -> KirkalabApiClient:
    return callback.bot.kirkalab_client


def _secret() -> str | None:
    return get_settings().bot_internal_secret


def parse_run_id(callback_data: str) -> int | None:
    """Extract the trailing run id from ``<ns>:xlsx:<id>`` callback data."""
    try:
        return int(callback_data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


async def handle_export(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    back_callback: str,
) -> None:
    """Run the export for the run id encoded in ``callback.data``.

    ``back_callback`` is the callback the upsell's "‹ Назад" button returns to
    (the result screen restart or the history list), so the FREE upsell never
    dead-ends. Always answers the callback so the client's spinner clears."""
    run_id = parse_run_id(callback.data or "")
    if run_id is None:
        await callback.answer()
        return

    secret = _secret()
    if not secret:
        await callback.answer("Экспорт недоступен.", show_alert=True)
        return

    try:
        content, filename = await _client(callback).export_calc_xlsx(
            telegram_user_id=callback.from_user.id,
            run_id=run_id,
            bot_secret=secret,
        )
    except ApiError as exc:
        if exc.status_code == 403:
            # FREE user: turn the live screen into the soft PRO upsell.
            await set_screen_id(state, callback.message.message_id)
            await edit_live_screen(
                callback.message,
                state,
                UPSELL_TEXT,
                reply_markup=export_upsell_kb(back_callback),
            )
            await callback.answer()
            return
        if exc.status_code == 404:
            await callback.answer(
                "Этот расчёт больше недоступен для экспорта.",
                show_alert=True,
            )
            return
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return

    document = BufferedInputFile(content, filename=filename)
    await callback.message.answer_document(document, caption=EXPORT_CAPTION)
    await callback.answer()
