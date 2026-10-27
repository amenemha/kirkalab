"""✉️ Написать менеджеру: сообщение уходит менеджерам, ответ приходит сюда же.

Менеджер может ответить прямо в Telegram — «Ответить» на пересланное ботом сообщение.
Любой свободный текст вне сценариев тоже передаётся менеджеру.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings

router = Router(name="support")

ASK = "✉️ Напишите сообщение одним текстом — я передам менеджеру. Ответ придёт сюда, в этот чат."
DONE = "✉️ Передала менеджеру. Ответ придёт сюда, в этот чат."


class SupportStates(StatesGroup):
  text = State()


def _hdr() -> dict:
  return {"X-Bot-Secret": get_settings().bot_internal_secret or ""}


def _menu_kb() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="☰ Меню", callback_data="hub:menu")]])


async def _deliver(message: Message) -> bool:
  u = message.from_user
  name = " ".join(x for x in (u.first_name, u.last_name) if x) or None
  r = await message.bot.kirkalab_client._request(
    "POST", "/api/v1/internal/support", headers=_hdr(),
    json={"telegram_user_id": u.id, "text": message.text, "username": u.username, "name": name})
  return r.status_code == 200


@router.message(Command("support"))
async def cmd_support(message: Message, state: FSMContext) -> None:
  await state.set_state(SupportStates.text)
  await message.answer(ASK)


@router.callback_query(F.data == "sup:write")
async def cb_write(cb: CallbackQuery, state: FSMContext) -> None:
  await state.set_state(SupportStates.text)
  await cb.answer()
  await cb.message.answer(ASK)


@router.message(SupportStates.text, F.text)
async def got_text(message: Message, state: FSMContext) -> None:
  if message.text.startswith("/"):
    await state.clear()
    return
  ok = await _deliver(message)
  await state.clear()
  await message.answer(DONE if ok else "Не получилось отправить — попробуйте ещё раз чуть позже.", reply_markup=_menu_kb())


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_text(message: Message) -> None:
  """Свободный текст: ответ менеджера на пересланное сообщение или новое сообщение менеджеру."""
  rt = message.reply_to_message
  if rt and rt.from_user and rt.from_user.id == message.bot.id:
    r = await message.bot.kirkalab_client._request(
      "POST", "/api/v1/internal/support/reply", headers=_hdr(),
      json={"manager_tg": message.from_user.id, "reply_to_message_id": rt.message_id, "text": message.text})
    if r.status_code == 200:
      await message.answer("✓ Ответ отправлен." if r.json().get("ok") else "Не удалось доставить ответ — человек мог остановить бота.")
      return
  ok = await _deliver(message)
  await message.answer(DONE if ok else "Не получилось отправить — попробуйте ещё раз чуть позже.", reply_markup=_menu_kb())
