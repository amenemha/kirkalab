"""Greeting + reply-keyboard main menu (CALC_SPEC §3.2).

The main menu is a persistent ``ReplyKeyboardMarkup`` (the "прибитая"
keyboard under the input field) with 4 buttons laid out 2×2:

  🧮 Калькулятор   📋 Каталог ASIC
  📊 Мои отчёты    👤 Профиль

Tapping a reply button sends its text as a plain message; ``cb_reply_menu``
matches that text and routes into the appropriate live-screen flow. Tariff/PRO
and Help are NOT top-level buttons — they live inline inside Profile.

The legacy inline ``menu:*`` callbacks are kept so the funnel/result keyboards
(which use ``menu:home``/``menu:catalog``/``menu:calculator``) still work.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.account import send_profile
from bot.handlers.history import open_history
from bot.keyboards import (
  STUB_ACTIONS,
  back_to_menu,
  calc_start_kb,
  main_reply_menu,
)
from bot.live_screen import edit_live_screen, safe_delete, set_screen_id
from bot.menu_items import REPLY_MENU_BY_TEXT

router = Router()

WELCOME_TEXT = (
  "👋 <b>Kirka</b> — ферма, пулы и расчёты ASIC.\n"
  "Главные кнопки всегда внизу, все разделы — в меню ниже."
)

CALC_START_TEXT = (
  "🧮 <b>Расчёт доходности</b>\n\n"
  "Выберите оборудование из каталога или введите параметры вручную."
)

STUB_TEXT = "🚧 Скоро будет доступно."


async def _open_catalog(message: Message, state: FSMContext) -> None:
  """Render the brand picker as a fresh live screen (reuses catalog data)."""
  client = message.bot.kirkalab_client
  from bot.api_client import ApiError
  from bot.keyboards import brand_list_kb

  try:
    brands = await client.list_brands()
  except ApiError as exc:
    await edit_live_screen(message, state, f"⚠️ {exc.message}")
    return
  text = (
    "📋 <b>Каталог ASIC</b>\n\nКаталог пока пуст."
    if not brands
    else "📋 <b>Каталог ASIC</b>\n\nВыберите бренд:"
  )
  await edit_live_screen(message, state, text, reply_markup=brand_list_kb(brands))


@router.message(Command("start"))
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext) -> None:
  from bot.handlers.hub import show_hub

  await state.set_state(None)
  await safe_delete(message)
  data = await state.get_data()
  # Remove the previous greeting so repeated /start does not pile up.
  if data.get("welcome_id"):
    from contextlib import suppress

    with suppress(Exception):
      await message.bot.delete_message(message.chat.id, data["welcome_id"])
  from bot.live_screen import drop_screen

  await drop_screen(message, state)
  sent = await message.answer(WELCOME_TEXT, reply_markup=main_reply_menu())
  await state.update_data(welcome_id=sent.message_id)
  await show_hub(message, state, message.from_user.id)


@router.message(F.text.func(lambda t: t in REPLY_MENU_BY_TEXT))
async def cb_reply_menu(message: Message, state: FSMContext) -> None:
  """Route the 4 persistent reply-keyboard buttons into their flows."""
  from bot.handlers.hub import show_farm, show_hub, show_problems

  action = REPLY_MENU_BY_TEXT[message.text]
  await safe_delete(message)
  await state.set_state(None)
  # Кнопки снизу: прошлый экран убираем, новый — один внизу чата.
  from bot.live_screen import drop_screen

  await drop_screen(message, state)
  uid = message.from_user.id
  if action == "farm":
    await show_farm(message, state, uid)
  elif action == "problems":
    await show_problems(message, state, uid)
  elif action == "menu":
    await show_hub(message, state, uid)
  elif action == "calculator":
    await edit_live_screen(
      message, state, CALC_START_TEXT, reply_markup=calc_start_kb()
    )
  elif action == "catalog":
    await _open_catalog(message, state)
  elif action == "reports":
    await open_history(message, message.from_user.id, state)
  elif action == "profile":
    await send_profile(message, message.from_user.id, state)


# --------------------------------------------------------------------------- #
# Legacy inline callbacks (used by result/funnel keyboards).
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
  from bot.handlers.hub import show_hub

  await set_screen_id(state, callback.message.message_id)
  await show_hub(callback.message, state, callback.from_user.id)
  await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery, state: FSMContext) -> None:
  from bot.handlers.hub import HELP_TEXT, btn
  from aiogram.types import InlineKeyboardMarkup

  await set_screen_id(state, callback.message.message_id)
  await edit_live_screen(callback.message, state, HELP_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("☰ Меню", "hub:menu")]]))
  await callback.answer()


@router.callback_query(F.data == "menu:reports")
async def cb_reports(callback: CallbackQuery, state: FSMContext) -> None:
  await set_screen_id(state, callback.message.message_id)
  await open_history(callback.message, callback.from_user.id, state)
  await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext) -> None:
  await set_screen_id(state, callback.message.message_id)
  await send_profile(callback.message, callback.from_user.id, state)
  await callback.answer()


@router.callback_query(F.data.startswith("menu:"))
async def cb_stub(callback: CallbackQuery, state: FSMContext) -> None:
  """Fallback for not-yet-implemented inline menu items."""
  action = callback.data.split(":", 1)[1]
  if action in STUB_ACTIONS:
    await set_screen_id(state, callback.message.message_id)
    await edit_live_screen(
      callback.message, state, STUB_TEXT, reply_markup=back_to_menu()
    )
  await callback.answer()
