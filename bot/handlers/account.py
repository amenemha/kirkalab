"""Profile + optional PRO account linking for the Kirkalab bot.

Auth model (changed per customer 20.06.2026):

* FREE users are authenticated **automatically by Telegram id** — there is no
  email login or registration to use the bot. The base cabinet is resolved
  server-side (``/internal/profile``) the first time it is needed.
* Email/password is an **optional** way to link this Telegram account to an
  existing PRO/web account on kirkalab.ru. It lives *inside Profile*, not as a
  gate in front of calculations or the catalog.

Commands:
  /health   - check API availability
  /me       - show the profile
  /cancel   - abort the current flow (e.g. the PRO link)
  /logout   - drop the in-memory PRO-link token

The PRO-link flow is a clean-chat FSM: prompts edit the single live screen and
the user's email/password messages are deleted right after they are read (so
secrets never linger). Tokens are kept only in process memory.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.api_client import ApiError, KirkalabApiClient
from bot.config import get_settings
from bot.handlers.tokens import token_store
from bot.keyboards import profile_menu
from bot.live_screen import edit_live_screen, safe_delete, set_screen_id
from bot.validation import EMAIL_HINT, looks_like_email

router = Router()

# Shared in-memory JWT storage: {telegram_user_id: access_token}.
_tokens = token_store

HELP_TEXT = (
  "ℹ️ <b>Помощь</b>\n\n"
  "Kirkalab помогает считать доходность ASIC, хранить отчёты и управлять "
  "тарифом.\n\n"
  "<b>Меню (кнопки внизу):</b>\n"
  "🧮 Калькулятор — расчёт доходности\n"
  "📋 Каталог ASIC — характеристики оборудования\n"
  "📊 Мои отчёты — сохранённые расчёты\n"
  "👤 Профиль — аккаунт, тариф и помощь\n\n"
  "Бесплатный доступ включается автоматически — ничего настраивать не нужно."
)


class LinkStates(StatesGroup):
  """Optional PRO-account linking by email + password."""

  email = State()
  password = State()


def _client(event: Message | CallbackQuery) -> KirkalabApiClient:
  return event.bot.kirkalab_client


def _secret() -> str | None:
  return get_settings().bot_internal_secret


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
  await safe_delete(message)
  if await state.get_state() is None:
    return
  await state.set_state(None)
  await edit_live_screen(
    message, state, "Действие отменено. Откройте меню кнопками внизу."
  )


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
  try:
    data = await _client(message).health()
  except ApiError as exc:
    await message.answer(f"⚠️ API недоступно: {exc.message}")
    return
  status = data.get("status", "ok") if isinstance(data, dict) else "ok"
  await message.answer(f"✅ API работает (статус: {status}).")


@router.message(Command("me"))
async def cmd_me(message: Message, state: FSMContext) -> None:
  await send_profile(message, message.from_user.id, state)


async def send_profile(
  message: Message, user_id: int, state: FSMContext
) -> None:
  """Render the Telegram-authenticated cabinet.

  FREE users are always "authorized" (auto-bound by telegram id), so this never
  shows a "not authorized" wall. Tariff/PRO and Help are offered as inline
  buttons here, keeping them out of the main menu."""
  secret = _secret()
  if not secret:
    await edit_live_screen(
      message,
      state,
      "⚠️ Бот не настроен. Обратитесь в поддержку.",
    )
    return
  try:
    profile = await _client(message).internal_profile(
      telegram_user_id=user_id, bot_secret=secret
    )
  except ApiError as exc:
    await edit_live_screen(
      message, state, f"❌ Не удалось открыть профиль: {exc.message}"
    )
    return

  is_pro = bool(profile.get("is_pro"))
  is_linked = bool(profile.get("is_linked"))
  plan = "💎 PRO" if is_pro else "🆓 Free"
  link_line = (
    "🔗 Аккаунт связан с PRO/веб-кабинетом"
    if is_linked
    else "🔗 PRO-аккаунт не привязан"
  )
  text = (
    "👤 <b>Ваш профиль</b>\n\n"
    f"🆔 Кабинет: #{profile.get('id')}\n"
    f"🏷 Тариф: {plan}\n"
    f"{link_line}\n\n"
    "Доступ к расчётам и каталогу включён автоматически."
  )
  await edit_live_screen(message, state, text, reply_markup=profile_menu(is_pro=is_pro))


@router.callback_query(F.data == "profile:help")
async def cb_profile_help(callback: CallbackQuery, state: FSMContext) -> None:
  await edit_live_screen(callback.message, state, HELP_TEXT)
  await callback.answer()


@router.callback_query(F.data == "profile:plan")
async def cb_profile_plan(callback: CallbackQuery, state: FSMContext) -> None:
  await edit_live_screen(
    callback.message,
    state,
    "💎 <b>PRO — скоро</b>\n\n"
    "Безлимитные расчёты, все валюты (₽/$/¥), окупаемость и ROI без блюра, "
    "сравнение прошивок и сохранение сборок.\n\n"
    "Подписка скоро будет доступна — спасибо, что вы с нами! 🙌",
  )
  await callback.answer()


# --------------------------------------------------------------------------- #
# Optional PRO-account link (email + password). Clean-chat FSM.
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "profile:link")
async def cb_profile_link(callback: CallbackQuery, state: FSMContext) -> None:
  await set_screen_id(state, callback.message.message_id)
  await state.set_state(LinkStates.email)
  await edit_live_screen(
    callback.message,
    state,
    "🔗 <b>Связать PRO-аккаунт</b>\n\n"
    "Введите email вашего аккаунта на kirkalab.ru (или /cancel):",
  )
  await callback.answer()


@router.message(LinkStates.email, F.text)
async def link_email(message: Message, state: FSMContext) -> None:
  email = (message.text or "").strip()
  await safe_delete(message)
  if not looks_like_email(email):
    await edit_live_screen(
      message,
      state,
      f"🔗 <b>Связать PRO-аккаунт</b>\n\n{EMAIL_HINT}\n\n"
      "Введите email ещё раз (или /cancel):",
    )
    return
  await state.update_data(link_email=email)
  await state.set_state(LinkStates.password)
  await edit_live_screen(
    message,
    state,
    "🔗 <b>Связать PRO-аккаунт</b>\n\nТеперь введите пароль (или /cancel):",
  )


@router.message(LinkStates.password, F.text)
async def link_password(message: Message, state: FSMContext) -> None:
  password = message.text or ""
  # Delete the password message immediately — never leave it in the chat.
  await safe_delete(message)
  data = await state.get_data()
  email = data.get("link_email", "")
  await state.set_state(None)
  try:
    token = await _client(message).login(email=email, password=password)
  except ApiError as exc:
    await edit_live_screen(
      message,
      state,
      "🔗 <b>Связать PRO-аккаунт</b>\n\n"
      f"❌ Не удалось войти: {exc.message}\n\n"
      "Проверьте email и пароль и попробуйте снова через Профиль.",
    )
    return
  _tokens[message.from_user.id] = token
  await edit_live_screen(
    message,
    state,
    "✅ <b>Аккаунт связан</b>\n\n"
    "Ваш Telegram привязан к аккаунту kirkalab.ru. "
    "PRO-возможности подключатся автоматически.",
  )


@router.message(Command("logout"))
async def cmd_logout(message: Message) -> None:
  if _tokens.pop(message.from_user.id, None) is None:
    await message.answer("PRO-аккаунт не был привязан.")
    return
  await message.answer("👋 Привязка PRO-аккаунта снята.")
