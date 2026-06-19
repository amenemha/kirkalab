"""aiogram handlers for the Kirkalab bot.

Implements the first client to the Kirkalab API:
  /start    - greeting and command overview
  /health   - check API availability
  /register - guided registration (email -> handle -> password)
  /login    - guided login (email -> password), stores a JWT in memory
  /me       - show the authenticated user's profile
  /logout   - drop the in-memory token
  /cancel   - abort the current flow

Tokens are kept only in process memory (never persisted), keyed by the
Telegram user id.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.api_client import ApiError, KirkalabApiClient

router = Router()

# In-memory JWT storage: {telegram_user_id: access_token}.
_tokens: dict[int, str] = {}


class RegisterStates(StatesGroup):
  email = State()
  handle = State()
  password = State()


class LoginStates(StatesGroup):
  email = State()
  password = State()


def _client(message: Message) -> KirkalabApiClient:
  return message.bot.kirkalab_client


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
  await message.answer(
    "Kirkalab bot.\n\n"
    "Available commands:\n"
    "/register - create an account\n"
    "/login - sign in\n"
    "/me - your profile\n"
    "/logout - sign out\n"
    "/health - API status\n"
    "/cancel - abort current action"
  )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
  if await state.get_state() is None:
    await message.answer("Nothing to cancel.")
    return
  await state.clear()
  await message.answer("Cancelled.")


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
  try:
    data = await _client(message).health()
  except ApiError as exc:
    await message.answer(f"API unavailable: {exc.message}")
    return
  status = data.get("status", "ok") if isinstance(data, dict) else "ok"
  await message.answer(f"API is healthy (status: {status}).")


@router.message(Command("register"))
async def cmd_register(message: Message, state: FSMContext) -> None:
  await state.set_state(RegisterStates.email)
  await message.answer("Registration. Send your email (or /cancel):")


@router.message(RegisterStates.email, F.text)
async def reg_email(message: Message, state: FSMContext) -> None:
  await state.update_data(email=message.text.strip())
  await state.set_state(RegisterStates.handle)
  await message.answer("Now send a handle (3-50 chars):")


@router.message(RegisterStates.handle, F.text)
async def reg_handle(message: Message, state: FSMContext) -> None:
  await state.update_data(handle=message.text.strip())
  await state.set_state(RegisterStates.password)
  await message.answer("Now send a password (min 8 chars):")


@router.message(RegisterStates.password, F.text)
async def reg_password(message: Message, state: FSMContext) -> None:
  data = await state.update_data(password=message.text)
  await state.clear()
  try:
    user = await _client(message).register(
      email=data["email"], handle=data["handle"], password=data["password"]
    )
  except ApiError as exc:
    await message.answer(f"Registration failed: {exc.message}")
    return
  await message.answer(
    f"Account created for {user.get('email')} (id {user.get('id')}). "
    "Use /login to sign in."
  )


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext) -> None:
  await state.set_state(LoginStates.email)
  await message.answer("Login. Send your email (or /cancel):")


@router.message(LoginStates.email, F.text)
async def login_email(message: Message, state: FSMContext) -> None:
  await state.update_data(email=message.text.strip())
  await state.set_state(LoginStates.password)
  await message.answer("Now send your password:")


@router.message(LoginStates.password, F.text)
async def login_password(message: Message, state: FSMContext) -> None:
  data = await state.update_data(password=message.text)
  await state.clear()
  try:
    token = await _client(message).login(
      email=data["email"], password=data["password"]
    )
  except ApiError as exc:
    await message.answer(f"Login failed: {exc.message}")
    return
  _tokens[message.from_user.id] = token
  await message.answer("Signed in. Use /me to view your profile.")


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
  token = _tokens.get(message.from_user.id)
  if token is None:
    await message.answer("You are not signed in. Use /login first.")
    return
  try:
    user = await _client(message).me(token)
  except ApiError as exc:
    await message.answer(f"Could not fetch profile: {exc.message}")
    return
  await message.answer(
    "Your profile:\n"
    f"id: {user.get('id')}\n"
    f"email: {user.get('email')}\n"
    f"handle: {user.get('handle')}\n"
    f"admin: {user.get('is_admin')}"
  )


@router.message(Command("logout"))
async def cmd_logout(message: Message) -> None:
  if _tokens.pop(message.from_user.id, None) is None:
    await message.answer("You were not signed in.")
    return
  await message.answer("Signed out.")
