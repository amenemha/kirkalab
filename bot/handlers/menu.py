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
  "👋 <b>Kirkalab</b> — ваш помощник по ASIC-майнингу.\n\n"
  "Бесплатный доступ включён автоматически. "
  "Выберите раздел кнопками внизу."
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
  await state.set_state(None)
  # A new live screen is created here; remember its id for in-place edits.
  sent = await message.answer(WELCOME_TEXT, reply_markup=main_reply_menu())
  await set_screen_id(state, sent.message_id)


@router.message(F.text.func(lambda t: t in REPLY_MENU_BY_TEXT))
async def cb_reply_menu(message: Message, state: FSMContext) -> None:
  """Route the 4 persistent reply-keyboard buttons into their flows."""
  action = REPLY_MENU_BY_TEXT[message.text]
  await safe_delete(message)
  await state.set_state(None)
  if action == "calculator":
    await edit_live_screen(
      message, state, CALC_START_TEXT, reply_markup=calc_start_kb()
    )
  elif action == "catalog":
    await _open_catalog(message, state)
  elif action == "reports":
    await edit_live_screen(message, state, STUB_TEXT, reply_markup=back_to_menu())
  elif action == "profile":
    await send_profile(message, message.from_user.id, state)


# --------------------------------------------------------------------------- #
# Legacy inline callbacks (used by result/funnel keyboards).
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
  await edit_live_screen(callback.message, state, WELCOME_TEXT)
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
