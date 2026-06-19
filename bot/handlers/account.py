"""Account command handlers for the Kirkalab bot.

Classic command-based flows backed by the Kirkalab API:
  /health   - check API availability
  /register - guided registration (email -> handle -> password)
  /login    - guided login (email -> password), stores a JWT in memory
  /me       - show the authenticated user's profile
  /logout   - drop the in-memory token
  /cancel   - abort the current flow

The greeting and inline navigation live in ``menu``; the QR deep-link
login lives in ``qr``. Tokens are kept only in process memory (never
persisted), keyed by the Telegram user id and shared via ``token_store``.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.api_client import ApiError, KirkalabApiClient
from bot.handlers.tokens import token_store

router = Router()

# Shared in-memory JWT storage: {telegram_user_id: access_token}.
_tokens = token_store


class RegisterStates(StatesGroup):
  email = State()
  handle = State()
  password = State()


class LoginStates(StatesGroup):
  email = State()
  password = State()


def _client(message: Message) -> KirkalabApiClient:
  return message.bot.kirkalab_client


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
  if await state.get_state() is None:
    await message.answer("Отменять нечего.")
    return
  await state.clear()
  await message.answer("Действие отменено.")


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
  try:
    data = await _client(message).health()
  except ApiError as exc:
    await message.answer(f"⚠️ API недоступно: {exc.message}")
    return
  status = data.get("status", "ok") if isinstance(data, dict) else "ok"
  await message.answer(f"✅ API работает (статус: {status}).")


@router.message(Command("register"))
async def cmd_register(message: Message, state: FSMContext) -> None:
  await state.set_state(RegisterStates.email)
  await message.answer("Регистрация. Отправьте ваш email (или /cancel):")


@router.message(RegisterStates.email, F.text)
async def reg_email(message: Message, state: FSMContext) -> None:
  await state.update_data(email=message.text.strip())
  await state.set_state(RegisterStates.handle)
  await message.answer("Теперь отправьте логин (3–50 символов):")


@router.message(RegisterStates.handle, F.text)
async def reg_handle(message: Message, state: FSMContext) -> None:
  await state.update_data(handle=message.text.strip())
  await state.set_state(RegisterStates.password)
  await message.answer("Теперь отправьте пароль (минимум 8 символов):")


@router.message(RegisterStates.password, F.text)
async def reg_password(message: Message, state: FSMContext) -> None:
  data = await state.update_data(password=message.text)
  await state.clear()
  try:
    user = await _client(message).register(
      email=data["email"], handle=data["handle"], password=data["password"]
    )
  except ApiError as exc:
    await message.answer(f"❌ Регистрация не удалась: {exc.message}")
    return
  await message.answer(
    f"✅ Аккаунт создан: {user.get('email')} (id {user.get('id')}).\n"
    "Войдите командой /login."
  )


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext) -> None:
  await state.set_state(LoginStates.email)
  await message.answer("Вход. Отправьте ваш email (или /cancel):")


@router.message(LoginStates.email, F.text)
async def login_email(message: Message, state: FSMContext) -> None:
  await state.update_data(email=message.text.strip())
  await state.set_state(LoginStates.password)
  await message.answer("Теперь отправьте пароль:")


@router.message(LoginStates.password, F.text)
async def login_password(message: Message, state: FSMContext) -> None:
  data = await state.update_data(password=message.text)
  await state.clear()
  try:
    token = await _client(message).login(
      email=data["email"], password=data["password"]
    )
  except ApiError as exc:
    await message.answer(f"❌ Вход не удался: {exc.message}")
    return
  _tokens[message.from_user.id] = token
  await message.answer("✅ Вы вошли. Профиль — командой /me или через меню.")


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
  await send_profile(message, message.from_user.id)


async def send_profile(message: Message, user_id: int) -> None:
  """Render the authenticated user's profile, or a hint to sign in.

  Shared between the /me command and the inline-menu "Профиль" button.
  """
  token = _tokens.get(user_id)
  if token is None:
    await message.answer(
      "Вы не авторизованы. Войдите командой /login."
    )
    return
  try:
    user = await _client(message).me(token)
  except ApiError as exc:
    await message.answer(f"❌ Не удалось получить профиль: {exc.message}")
    return
  await message.answer(
    "👤 <b>Ваш профиль</b>\n\n"
    f"🆔 ID: {user.get('id')}\n"
    f"✉️ Email: {user.get('email')}\n"
    f"🔖 Логин: {user.get('handle')}\n"
    f"⭐ Админ: {'да' if user.get('is_admin') else 'нет'}"
  )


@router.message(Command("logout"))
async def cmd_logout(message: Message) -> None:
  if _tokens.pop(message.from_user.id, None) is None:
    await message.answer("Вы не были авторизованы.")
    return
  await message.answer("👋 Вы вышли из аккаунта.")
