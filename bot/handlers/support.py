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

ASK = "✉️ Напишите сообщение или пришлите скриншот, фото, файл — я передам менеджеру. Ответ придёт сюда, в этот чат."
DONE = "✉️ Передала менеджеру. Ответ придёт сюда, в этот чат."


class SupportStates(StatesGroup):
  text = State()


def _hdr() -> dict:
  return {"X-Bot-Secret": get_settings().bot_internal_secret or ""}


def _menu_kb() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="☰ Меню", callback_data="hub:menu")]])


MEDIA = F.photo | F.document | F.video | F.voice | F.audio
_groups: dict[str, float] = {}


def file_of(message: Message) -> dict | None:
  """Вложение сообщения в виде {type, file_id, name, size, mime}."""
  if message.photo:
    p = message.photo[-1]
    return {"type": "photo", "file_id": p.file_id, "size": p.file_size}
  for k in ("document", "video", "voice", "audio"):
    d = getattr(message, k, None)
    if d:
      return {"type": k, "file_id": d.file_id, "name": getattr(d, "file_name", None), "size": d.file_size,
              "mime": getattr(d, "mime_type", None)}
  return None


def _first_in_group(message: Message) -> bool:
  """Альбом приходит пачкой сообщений — отвечаем «передала» один раз."""
  g = message.media_group_id
  if not g:
    return True
  import time
  now = time.monotonic()
  for k in [k for k, t in _groups.items() if now - t > 120]:
    _groups.pop(k, None)
  if g in _groups:
    return False
  _groups[g] = now
  return True


def quote_of(message: Message) -> dict:
  """На что человек отвечает: цитата (или весь текст сообщения) и ссылка «Открыть эти асики», если была."""
  rm = message.reply_to_message
  if rm is None:
    return {}
  q = message.quote.text if getattr(message, "quote", None) and message.quote.text else (rm.text or rm.caption or "")
  if not q:
    q = "фото" if rm.photo else "файл" if rm.document else "сообщение"
  out = {"quote": q[:1500], "quote_bot": bool(rm.from_user and rm.from_user.is_bot)}
  kb = getattr(rm, "reply_markup", None)
  for row in (kb.inline_keyboard if kb else []):
    for b in row:
      if b.url and "#ev/" in b.url:
        out["quote_link"] = b.url
  return out


async def _deliver(message: Message) -> bool:
  u = message.from_user
  name = " ".join(x for x in (u.first_name, u.last_name) if x) or None
  body = {"telegram_user_id": u.id, "text": message.text or message.caption or "", "username": u.username, "name": name}
  f = file_of(message)
  if f:
    body["file"] = f
  body.update(quote_of(message))
  r = await message.bot.kirkalab_client._request("POST", "/api/v1/internal/support", headers=_hdr(), json=body)
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


@router.message(SupportStates.text, F.text | MEDIA)
async def got_text(message: Message, state: FSMContext) -> None:
  if message.text and message.text.startswith("/"):
    await state.clear()
    return
  ok = await _deliver(message)
  await state.clear()
  if _first_in_group(message):
    await message.answer(DONE if ok else "Не получилось отправить — попробуйте ещё раз чуть позже.", reply_markup=_menu_kb())


@router.message(StateFilter(None), (F.text & ~F.text.startswith("/")) | MEDIA)
async def free_text(message: Message) -> None:
  """Свободный текст или файл: ответ менеджера на пересланное сообщение или новое сообщение менеджеру."""
  rt = message.reply_to_message
  if rt and rt.from_user and rt.from_user.id == message.bot.id:
    body = {"manager_tg": message.from_user.id, "reply_to_message_id": rt.message_id, "text": message.text or message.caption or ""}
    f = file_of(message)
    if f:
      body["file"] = f
    r = await message.bot.kirkalab_client._request("POST", "/api/v1/internal/support/reply", headers=_hdr(), json=body)
    if r.status_code == 200:
      await message.answer("✓ Ответ отправлен." if r.json().get("ok") else "Не удалось доставить ответ — человек мог остановить бота.")
      return
  ok = await _deliver(message)
  if _first_in_group(message):
    await message.answer(DONE if ok else "Не получилось отправить — попробуйте ещё раз чуть позже.", reply_markup=_menu_kb())
