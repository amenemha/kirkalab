"""Clean-chat live-screen helpers: edit-in-place, fallback, swallowed deletes.

Uses minimal fakes for the aiogram Bot/Message so no Telegram network is
touched, and drives the coroutines with ``asyncio.run`` (the test env has no
pytest-asyncio). Skipped when aiogram is unavailable.
"""
import asyncio

import pytest

pytest.importorskip("aiogram")

from aiogram.exceptions import TelegramBadRequest

from bot.live_screen import (
    SCREEN_KEY,
    edit_live_screen,
    get_screen_id,
    safe_delete,
    set_screen_id,
)


class FakeState:
    """Stand-in for aiogram FSMContext data storage."""

    def __init__(self) -> None:
        self._data: dict = {}

    async def update_data(self, **kwargs):
        self._data.update(kwargs)
        return self._data

    async def get_data(self):
        return dict(self._data)


def _bad_request(msg: str) -> TelegramBadRequest:
    # TelegramBadRequest's signature varies across aiogram patch versions;
    # build it defensively so the test is version-tolerant.
    try:
        return TelegramBadRequest(method=None, message=msg)
    except TypeError:  # pragma: no cover - older/newer aiogram
        return TelegramBadRequest(message=msg)


class FakeBot:
    def __init__(self, *, edit_raises=False) -> None:
        self.edit_raises = edit_raises
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self._next_id = 1000

    async def edit_message_text(self, **kwargs):
        if self.edit_raises:
            raise _bad_request("message not found")
        self.edited.append(kwargs)
        return FakeMessage(self, message_id=kwargs["message_id"])

    async def send_message(self, **kwargs):
        self._next_id += 1
        self.sent.append({**kwargs, "message_id": self._next_id})
        return FakeMessage(self, message_id=self._next_id)


class FakeChat:
    id = 42


class FakeMessage:
    def __init__(self, bot, message_id=1) -> None:
        self.bot = bot
        self.message_id = message_id
        self.chat = FakeChat()
        self.deleted = False

    async def delete(self):
        self.deleted = True


def test_set_and_get_screen_id():
    async def run():
        state = FakeState()
        await set_screen_id(state, 777)
        assert await get_screen_id(state) == 777

    asyncio.run(run())


def test_edit_live_screen_edits_existing():
    async def run():
        bot = FakeBot()
        state = FakeState()
        await set_screen_id(state, 500)
        await edit_live_screen(FakeMessage(bot, message_id=1), state, "hi")
        assert len(bot.edited) == 1
        assert bot.edited[0]["message_id"] == 500
        assert not bot.sent

    asyncio.run(run())


def test_edit_live_screen_sends_when_no_screen():
    async def run():
        bot = FakeBot()
        state = FakeState()
        await edit_live_screen(FakeMessage(bot, message_id=1), state, "hi")
        assert len(bot.sent) == 1
        assert (await state.get_data())[SCREEN_KEY] == bot.sent[0]["message_id"]

    asyncio.run(run())


def test_edit_live_screen_falls_back_on_edit_failure():
    async def run():
        bot = FakeBot(edit_raises=True)
        state = FakeState()
        await set_screen_id(state, 500)
        await edit_live_screen(FakeMessage(bot, message_id=1), state, "hi")
        assert len(bot.sent) == 1
        assert (await state.get_data())[SCREEN_KEY] == bot.sent[0]["message_id"]

    asyncio.run(run())


def test_safe_delete_swallows_errors():
    class Boom(FakeMessage):
        async def delete(self):
            raise _bad_request("already gone")

    asyncio.run(safe_delete(Boom(FakeBot())))  # must not raise
