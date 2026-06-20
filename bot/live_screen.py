"""Clean-chat helpers: one editable "live screen" + ephemeral input removal.

Implements the CALC_SPEC §3.2 model "один экран = одно редактируемое сообщение":

* The bot keeps a single *live screen* message per chat (its id is stored in the
  FSM context) and updates it in place with ``editMessageText`` rather than
  spamming new messages.
* The user's own input messages (email, password, numbers) are deleted after
  they are read, so secrets don't linger and the chat stays tidy.

All Telegram calls that can legitimately fail (message already gone, too old to
edit, no delete permission) are swallowed — the helpers never raise.

These helpers are intentionally tiny and aiogram-typed; tests that import them
guard with ``pytest.importorskip("aiogram")``.
"""
from __future__ import annotations

from contextlib import suppress

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

# FSM-data key under which the live screen's message id is stored.
SCREEN_KEY = "live_screen_id"

_DELETE_ERRORS = (TelegramBadRequest, TelegramForbiddenError)


async def set_screen_id(state: FSMContext, message_id: int | None) -> None:
    await state.update_data(**{SCREEN_KEY: message_id})


async def get_screen_id(state: FSMContext) -> int | None:
    data = await state.get_data()
    return data.get(SCREEN_KEY)


async def safe_delete(message: Message) -> None:
    """Delete a message, ignoring "already gone / not allowed" failures."""
    with suppress(*_DELETE_ERRORS):
        await message.delete()


async def edit_live_screen(
    message: Message,
    state: FSMContext,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_web_page_preview: bool = True,
) -> Message:
    """Update the stored live screen in place, or create one if none exists.

    ``message`` is any message in the target chat (e.g. the user's input or a
    callback's message). When a live-screen id is stored, it is edited; if the
    edit is impossible (deleted/too old), a fresh screen is sent and remembered.
    """
    screen_id = await get_screen_id(state)
    bot = message.bot
    chat_id = message.chat.id

    if screen_id is not None:
        try:
            edited = await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=screen_id,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            # edit_message_text returns True for inaccessible messages; only a
            # real Message carries an id worth keeping.
            if isinstance(edited, Message):
                return edited
            return message
        except TelegramBadRequest:
            # Message gone or unchanged — fall through to send a new one.
            pass

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )
    await set_screen_id(state, sent.message_id)
    return sent
