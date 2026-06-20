"""AdminNotifier integration tests (Queue 2.4).

Exercises the aiogram-dependent notifier with a fake Bot so no Telegram traffic
happens. Skipped when aiogram is absent (backend CI); the pure throttle/masking
logic is covered separately in test_bot_alerts.py.
"""
import asyncio

import pytest

pytest.importorskip("aiogram")

from bot.notifier import AdminNotifier


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def test_notifier_disabled_without_admin_chat():
    bot = FakeBot()
    notifier = AdminNotifier(bot, admin_chat_id=None)

    assert notifier.enabled is False
    sent = asyncio.run(notifier.alert("anything"))
    assert sent is False
    assert bot.sent == []


def test_notifier_sends_masked_message():
    bot = FakeBot()
    notifier = AdminNotifier(bot, admin_chat_id=42)

    sent = asyncio.run(notifier.alert("password=hunter2 leaked"))

    assert sent is True
    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == 42
    assert "hunter2" not in text


def test_notifier_throttles_same_key():
    bot = FakeBot()
    notifier = AdminNotifier(bot, admin_chat_id=42, throttle_interval_seconds=999)

    first = asyncio.run(notifier.alert_backend_error("5xx", "GET /x -> 500"))
    second = asyncio.run(notifier.alert_backend_error("5xx", "GET /y -> 503"))

    assert first is True
    assert second is False
    assert len(bot.sent) == 1


def test_notifier_swallows_send_failures():
    class BrokenBot:
        async def send_message(self, *_a, **_k):
            raise RuntimeError("telegram down")

    notifier = AdminNotifier(BrokenBot(), admin_chat_id=42)
    # Must not raise even though delivery fails.
    sent = asyncio.run(notifier.alert("boom"))
    assert sent is False
