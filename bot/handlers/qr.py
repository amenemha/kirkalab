"""QR deep-link login handler.

Closes the QR-authorization loop with the backend:

  1. The website calls POST /api/v1/auth/qr/start and renders a QR code that
     encodes the deep link ``https://t.me/<bot>?start=qr_<session_id>``.
  2. The user scans it; Telegram opens the bot with ``/start qr_<session_id>``.
  3. The bot shows inline buttons "✅ Подтвердить вход" / "❌ Отклонить".
  4. On confirm, the bot calls POST /api/v1/auth/qr/approve with the
     ``X-Bot-Secret`` header and body {"session_id", "telegram_user_id"}.
  5. The website, polling /status/{session_id}, receives the tokens.

The pending session id is held per-user in the FSM context between the
deep-link message and the button press, so no extra state store is needed.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from contextlib import suppress

from aiogram.types import InlineKeyboardMarkup

from bot.api_client import ApiError, KirkalabApiClient
from bot.config import get_settings
from bot.deep_link import parse_qr_payload
from bot.keyboards import qr_confirm

router = Router()


class QrStates(StatesGroup):
  confirm = State()


def _client(event: Message | CallbackQuery) -> KirkalabApiClient:
  return event.bot.kirkalab_client


@router.message(CommandStart(deep_link=True))
async def qr_deep_link(
  message: Message, command: CommandObject, state: FSMContext
) -> None:
  """Handle ``/start qr_<session_id>``.

  Falls back to the normal greeting/menu when the payload is not a QR one.
  """
  session_id = parse_qr_payload(command.args)
  if session_id is None:
    # Not a QR deep link — defer to the regular menu greeting.
    from bot.handlers.menu import cmd_start

    await cmd_start(message, state)
    return

  await state.set_state(QrStates.confirm)
  await state.update_data(qr_session_id=session_id, qr_start_id=message.message_id)
  await message.answer(
    "🔐 <b>Запрос на вход через QR</b>\n\n"
    "Кто-то (вероятно, вы) пытается войти на сайт Kirkalab "
    "с помощью этого QR-кода.\n\n"
    "Подтвердите вход, если это вы.",
    reply_markup=qr_confirm(),
  )


@router.callback_query(QrStates.confirm, F.data == "qr:approve")
async def qr_approve(callback: CallbackQuery, state: FSMContext) -> None:
  data = await state.get_data()
  session_id = data.get("qr_session_id")
  start_id, prev_ok = data.get("qr_start_id"), data.get("qr_ok_id")
  await state.set_state(None)
  await state.update_data(qr_session_id=None, qr_start_id=None)

  if not session_id:
    await callback.message.edit_text(
      "⚠️ Сессия входа не найдена. Запросите QR-код заново на сайте."
    )
    await callback.answer()
    return

  settings = get_settings()
  if not settings.bot_internal_secret:
    await callback.message.edit_text(
      "⚠️ Бот не настроен для подтверждения входа. "
      "Обратитесь в поддержку."
    )
    await callback.answer()
    return

  try:
    await _client(callback).approve_qr(
      session_id=session_id,
      telegram_user_id=callback.from_user.id,
      bot_secret=settings.bot_internal_secret,
    )
  except ApiError as exc:
    if exc.status_code in (404, 409):
      text = (
        "⌛ Срок действия QR-кода истёк или он уже использован.\n"
        "Запросите новый QR-код на сайте."
      )
    else:
      text = f"❌ Не удалось подтвердить вход: {exc.message}"
    await callback.message.edit_text(text)
    await callback.answer()
    return

  from bot.handlers.hub import SITE, btn

  chat = callback.message.chat.id
  # Keep the chat clean: drop the /start message and the previous confirmation.
  for mid in (start_id, prev_ok):
    if mid and mid != callback.message.message_id:
      with suppress(Exception):
        await callback.bot.delete_message(chat, mid)
  await callback.message.edit_text(
    "✅ <b>Вход подтверждён</b>\n\nСайт уже открыт — вернитесь во вкладку браузера.",
    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
      [btn("↩️ Вернуться на сайт", url=SITE, style="success")],
      [btn("☰ Меню бота", "hub:menu")],
    ]),
  )
  await state.update_data(qr_ok_id=callback.message.message_id)
  await callback.answer("Готово")


@router.callback_query(QrStates.confirm, F.data == "qr:reject")
async def qr_reject(callback: CallbackQuery, state: FSMContext) -> None:
  data = await state.get_data()
  await state.set_state(None)
  if data.get("qr_start_id"):
    with suppress(Exception):
      await callback.bot.delete_message(callback.message.chat.id, data["qr_start_id"])
  await callback.message.edit_text(
    "❌ Вход отклонён. Если это были не вы — всё в порядке, "
    "никаких действий не требуется."
  )
  await callback.answer()
