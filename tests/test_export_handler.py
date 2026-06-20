"""Bot-side export action tests (Queue 2.2): PRO file vs FREE upsell vs errors.

Uses minimal fakes for aiogram Bot/Message/Callback + a stub API client so no
Telegram or HTTP is touched. Skipped when aiogram is unavailable (CI)."""
import asyncio

import pytest

pytest.importorskip("aiogram")

from bot.api_client import ApiError
from bot.handlers.export import EXPORT_CAPTION, handle_export, parse_run_id


class FakeState:
    def __init__(self) -> None:
        self._data: dict = {}

    async def update_data(self, **kwargs):
        self._data.update(kwargs)
        return self._data

    async def get_data(self):
        return dict(self._data)


class FakeClient:
    def __init__(self, *, result=None, error=None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    async def export_calc_xlsx(self, *, telegram_user_id, run_id, bot_secret):
        self.calls.append(
            {"telegram_user_id": telegram_user_id, "run_id": run_id}
        )
        if self._error is not None:
            raise self._error
        return self._result


class FakeBot:
    def __init__(self, client) -> None:
        self.kirkalab_client = client
        self.edited: list[dict] = []

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return FakeMessage(self, message_id=kwargs["message_id"])


class FakeChat:
    id = 7


class FakeMessage:
    def __init__(self, bot, message_id=1) -> None:
        self.bot = bot
        self.message_id = message_id
        self.chat = FakeChat()
        self.documents: list[dict] = []

    async def answer_document(self, document, caption=None):
        self.documents.append({"document": document, "caption": caption})
        return self


class FakeUser:
    id = 555


class FakeCallback:
    def __init__(self, bot, data: str) -> None:
        self.bot = bot
        self.data = data
        self.from_user = FakeUser()
        self.message = FakeMessage(bot, message_id=10)
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    # handle_export reads the bot secret from settings; stub a present one.
    from bot import config

    class _S:
        bot_internal_secret = "s3cret"

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    # bot.handlers.export imported get_settings into its own namespace.
    from bot.handlers import export as export_mod

    monkeypatch.setattr(export_mod, "get_settings", lambda: _S())
    yield


def test_parse_run_id():
    assert parse_run_id("calc:xlsx:42") == 42
    assert parse_run_id("hist:xlsx:7") == 7
    assert parse_run_id("calc:xlsx:nope") is None
    assert parse_run_id("") is None


def test_pro_export_sends_document():
    async def run():
        client = FakeClient(result=(b"PK\x03\x04xlsxbytes", "kirkalab_calc_42.xlsx"))
        bot = FakeBot(client)
        cb = FakeCallback(bot, "calc:xlsx:42")
        await handle_export(cb, FakeState(), back_callback="calc:restart")
        # The document is sent as a new message (kept) — clean-chat: no edit.
        assert len(cb.message.documents) == 1
        assert cb.message.documents[0]["caption"] == EXPORT_CAPTION
        assert client.calls[0]["run_id"] == 42
        assert not bot.edited
        assert cb.answers  # callback acknowledged

    asyncio.run(run())


def test_free_user_sees_upsell_edit():
    async def run():
        client = FakeClient(error=ApiError("PRO only", status_code=403))
        bot = FakeBot(client)
        cb = FakeCallback(bot, "hist:xlsx:9")
        await handle_export(cb, FakeState(), back_callback="hist:list")
        # No document; the live screen is edited into the upsell.
        assert not cb.message.documents
        assert len(bot.edited) == 1
        assert "PRO" in bot.edited[0]["text"]
        # Upsell keyboard offers the tariff + a back button to the caller.
        cbs = [
            b.callback_data
            for row in bot.edited[0]["reply_markup"].inline_keyboard
            for b in row
        ]
        assert "profile:plan" in cbs
        assert "hist:list" in cbs

    asyncio.run(run())


def test_expired_run_shows_alert():
    async def run():
        client = FakeClient(error=ApiError("gone", status_code=404))
        bot = FakeBot(client)
        cb = FakeCallback(bot, "calc:xlsx:5")
        await handle_export(cb, FakeState(), back_callback="calc:restart")
        assert not cb.message.documents
        assert not bot.edited
        assert cb.answers[-1]["show_alert"] is True

    asyncio.run(run())


def test_bad_run_id_just_answers():
    async def run():
        client = FakeClient(result=(b"x", "f.xlsx"))
        bot = FakeBot(client)
        cb = FakeCallback(bot, "calc:xlsx:bad")
        await handle_export(cb, FakeState(), back_callback="calc:restart")
        assert not client.calls  # never reached the API
        assert cb.answers

    asyncio.run(run())
