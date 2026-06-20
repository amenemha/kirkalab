"""«Мои отчёты / История» — список сохранённых расчётов (Queue 2.3).

Живой экран (CALC_SPEC §3.2): один редактируемый message, без новых сообщений.
Список расчётов (последние сверху, по странице) рендерится через
``editMessageText``; тап по отчёту открывает детальный экран, «Назад» — обратно
к списку.

Retention применяется на сервере (``/internal/history``): на FREE показываются
только расчёты за последние ``free_history_retention_days`` дней, на PRO — вся
история. Здесь только отрисовка и навигация — бизнес-правила не дублируются.

Callbacks (``hist:*`` namespace):
  hist:p:<page>      перейти на страницу списка
  hist:open:<id>     открыть детальный экран расчёта
  hist:list          вернуться к списку из детального экрана
  hist:noop          индикатор страницы (без действия)
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api_client import ApiError, KirkalabApiClient
from bot.config import get_settings
from bot.handlers.export import handle_export
from bot.history_format import (
    PAGE_SIZE,
    format_detail_screen,
    format_empty_screen,
    format_list_screen,
    page_count,
    page_offset,
)
from bot.keyboards import (
    back_to_menu,
    history_detail_kb,
    history_empty_kb,
    history_list_kb,
)
from bot.live_screen import edit_live_screen, set_screen_id

router = Router()


def _client(event: Message | CallbackQuery) -> KirkalabApiClient:
    return event.bot.kirkalab_client


def _secret() -> str | None:
    return get_settings().bot_internal_secret


async def open_history(message: Message, user_id: int, state: FSMContext) -> None:
    """Render the history list as a live screen (entry from the reply menu)."""
    await _render_list(message, user_id, state, page=0)


async def _render_list(
    message: Message, user_id: int, state: FSMContext, *, page: int
) -> None:
    secret = _secret()
    if not secret:
        await edit_live_screen(
            message, state, "⚠️ Бот не настроен. Обратитесь в поддержку.",
            reply_markup=back_to_menu(),
        )
        return
    try:
        data = await _client(message).list_history(
            telegram_user_id=user_id, bot_secret=secret, page=page
        )
    except ApiError as exc:
        await edit_live_screen(
            message, state, f"❌ Не удалось открыть отчёты: {exc.message}",
            reply_markup=back_to_menu(),
        )
        return

    items = data.get("items", [])
    total = int(data.get("total", 0))
    is_pro = bool(data.get("is_pro"))
    truncated = bool(data.get("truncated"))
    retention_days = int(data.get("retention_days", 0))

    if not items:
        await edit_live_screen(
            message, state, format_empty_screen(), reply_markup=history_empty_kb()
        )
        return

    page = int(data.get("page", page))
    last_page = page_count(total, PAGE_SIZE) - 1
    text = format_list_screen(
        items,
        page=page,
        total=total,
        is_pro=is_pro,
        truncated=truncated,
        retention_days=retention_days,
    )
    kb = history_list_kb(
        items,
        page=page,
        last_page=last_page,
        start_index=page_offset(page, PAGE_SIZE) + 1,
        show_pro=truncated and not is_pro,
    )
    await edit_live_screen(message, state, text, reply_markup=kb)


@router.callback_query(F.data == "hist:noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("hist:p:"))
async def cb_page(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        page = 0
    await set_screen_id(state, callback.message.message_id)
    await _render_list(callback.message, callback.from_user.id, state, page=max(page, 0))
    await callback.answer()


@router.callback_query(F.data == "hist:list")
async def cb_back_to_list(callback: CallbackQuery, state: FSMContext) -> None:
    await set_screen_id(state, callback.message.message_id)
    await _render_list(callback.message, callback.from_user.id, state, page=0)
    await callback.answer()


@router.callback_query(F.data.startswith("hist:open:"))
async def cb_open(callback: CallbackQuery, state: FSMContext) -> None:
    secret = _secret()
    try:
        run_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    if not secret:
        await callback.answer("Недоступно.", show_alert=True)
        return
    try:
        item = await _client(callback).get_history_run(
            telegram_user_id=callback.from_user.id, run_id=run_id, bot_secret=secret
        )
    except ApiError as exc:
        if exc.status_code == 404:
            await callback.answer(
                "Этот расчёт больше недоступен (истёк срок хранения).",
                show_alert=True,
            )
            await set_screen_id(state, callback.message.message_id)
            await _render_list(callback.message, callback.from_user.id, state, page=0)
            return
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return

    await set_screen_id(state, callback.message.message_id)
    await edit_live_screen(
        callback.message,
        state,
        format_detail_screen(item),
        reply_markup=history_detail_kb(item.get("id")),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:xlsx:"))
async def cb_export(callback: CallbackQuery, state: FSMContext) -> None:
    # Excel export from the history detail screen. PRO gate + ownership are
    # enforced server-side; a FREE user gets a soft upsell whose "back" returns
    # to the history list.
    await handle_export(callback, state, back_callback="hist:list")
