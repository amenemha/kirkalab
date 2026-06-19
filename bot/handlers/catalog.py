"""ASIC catalog navigation: Brand -> Model -> Device card (Rapira style).

A text-card + inline-button-rows flow, mirroring the rest of the bot. All
data comes from the Kirkalab API (``devices``/``firmware`` endpoints) via the
shared :class:`KirkalabApiClient` — the bot never touches the database.

Callback-data scheme (kept well under Telegram's 64-byte limit by using ids
and short prefixes, never long model names):

  ``cat:home``                 brand picker
  ``cat:b:<brand>:<page>``     model page for a brand (0-based page)
  ``cat:m:<model_id>``         device card
  ``cat:fw:<model_id>``        firmware presets for a model
  ``cat:noop``                 inert button (e.g. the page indicator)
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.api_client import ApiError, KirkalabApiClient
from bot.catalog_format import format_device_card, format_firmware_list
from bot.keyboards import (
    PAGE_SIZE,
    brand_list_kb,
    device_card_kb,
    firmware_back_kb,
    model_page_kb,
)

router = Router()


def _client(callback: CallbackQuery) -> KirkalabApiClient:
    return callback.message.bot.kirkalab_client


@router.callback_query(F.data == "cat:noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "menu:catalog")
@router.callback_query(F.data == "cat:home")
async def cb_brands(callback: CallbackQuery) -> None:
    """Render the brand picker (first catalog screen)."""
    try:
        brands = await _client(callback).list_brands()
    except ApiError as exc:
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return
    if not brands:
        await callback.message.edit_text(
            "📋 <b>Каталог ASIC</b>\n\nКаталог пока пуст.",
            reply_markup=brand_list_kb([]),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "📋 <b>Каталог ASIC</b>\n\nВыберите бренд:",
        reply_markup=brand_list_kb(brands),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cat:b:"))
async def cb_models(callback: CallbackQuery) -> None:
    """Render one page of models for the chosen brand."""
    # cat:b:<brand>:<page> — split from the right so brand names with ':'
    # would still work (none do, but it keeps parsing robust).
    _, _, brand, page_str = callback.data.split(":", 3)
    try:
        page = int(page_str)
    except ValueError:
        page = 0
    skip = page * PAGE_SIZE
    try:
        data = await _client(callback).list_models_by_brand(
            brand, skip=skip, limit=PAGE_SIZE
        )
    except ApiError as exc:
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return

    items = data.get("items", [])
    total = data.get("total", 0)
    if not items:
        await callback.answer("Модели не найдены.", show_alert=True)
        return

    last_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(page, last_page)
    text = (
        f"📋 <b>{brand}</b>\n\n"
        f"Моделей: {total}. Страница {page + 1} из {last_page + 1}.\n"
        "Выберите модель:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=model_page_kb(
            brand=brand, items=items, page=page, last_page=last_page
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cat:m:"))
async def cb_card(callback: CallbackQuery) -> None:
    """Render the full passport card for a device model."""
    model_id = _parse_id(callback.data)
    if model_id is None:
        await callback.answer()
        return
    client = _client(callback)
    try:
        model = await client.get_model(model_id)
        presets = await client.list_firmware_presets(model_id)
    except ApiError as exc:
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return

    await callback.message.edit_text(
        format_device_card(model),
        reply_markup=device_card_kb(
            brand=model.get("brand", ""),
            model_id=model_id,
            has_firmware=bool(presets),
        ),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cat:fw:"))
async def cb_firmware(callback: CallbackQuery) -> None:
    """Show the firmware tuning presets available for a model."""
    model_id = _parse_id(callback.data)
    if model_id is None:
        await callback.answer()
        return
    client = _client(callback)
    try:
        model = await client.get_model(model_id)
        presets = await client.list_firmware_presets(model_id)
    except ApiError as exc:
        await callback.answer(f"⚠️ {exc.message}", show_alert=True)
        return

    if not presets:
        await callback.answer(
            "Для этой модели нет кастомных прошивок.", show_alert=True
        )
        return

    await callback.message.edit_text(
        format_firmware_list(model, presets),
        reply_markup=firmware_back_kb(model_id),
        disable_web_page_preview=True,
    )
    await callback.answer()


def _parse_id(data: str) -> int | None:
    """Extract the trailing integer id from a ``cat:*:<id>`` callback."""
    try:
        return int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
