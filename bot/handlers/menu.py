"""Greeting and inline-menu navigation (Rapira style).

Owns the plain ``/start`` (no payload), the ``/menu`` command and all
``menu:*`` callbacks. New menu items are added by extending
``MAIN_MENU_ITEMS`` in ``bot.keyboards`` and (if interactive) handling their
action here — no changes to unrelated handlers are required.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers.account import send_profile
from bot.keyboards import STUB_ACTIONS, back_to_menu, main_menu

router = Router()

WELCOME_TEXT = (
  "👋 <b>Kirkalab</b> — ваш помощник по ASIC-майнингу.\n\n"
  "Выберите раздел в меню ниже."
)

HELP_TEXT = (
  "ℹ️ <b>Помощь</b>\n\n"
  "Kirkalab помогает считать доходность ASIC, хранить отчёты "
  "и управлять тарифом.\n\n"
  "<b>Меню:</b>\n"
  "👤 Профиль — данные вашего аккаунта\n"
  "🧮 Калькулятор ASIC — расчёт доходности\n"
  "📋 Каталог ASIC — характеристики оборудования\n"
  "📊 Мои отчёты — сохранённые расчёты\n"
  "💎 Тариф — ваш план и лимиты\n\n"
  "<b>Команды:</b>\n"
  "/menu — главное меню\n"
  "/register — регистрация\n"
  "/login — вход по email\n"
  "/me — профиль\n"
  "/logout — выход\n"
  "/health — статус API"
)

STUB_TEXT = "🚧 Скоро будет доступно."


async def show_welcome(message: Message) -> None:
  """Send the greeting with the main menu. Reused by the QR handler."""
  await message.answer(WELCOME_TEXT, reply_markup=main_menu())


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
  await show_welcome(message)


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
  await show_welcome(message)


@router.callback_query(F.data == "menu:home")
async def cb_home(callback: CallbackQuery) -> None:
  await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu())
  await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery) -> None:
  await send_profile(callback.message, callback.from_user.id)
  await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
  await callback.message.edit_text(HELP_TEXT, reply_markup=back_to_menu())
  await callback.answer()


@router.callback_query(F.data.startswith("menu:"))
async def cb_stub(callback: CallbackQuery) -> None:
  """Fallback for not-yet-implemented menu items."""
  action = callback.data.split(":", 1)[1]
  if action in STUB_ACTIONS:
    await callback.message.edit_text(STUB_TEXT, reply_markup=back_to_menu())
  await callback.answer()
