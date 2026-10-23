"""Telegram session with quick retries.

С сервера часть соединений до api.telegram.org зависает. Запрос
повторяется до 4 раз с коротким таймаутом соединения, чтобы пользователь
не ждал 60 секунд и не получал ошибку.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger("kirkalab.bot.session")

ATTEMPTS = 4


class RetrySession(AiohttpSession):
    async def make_request(self, bot, method, timeout=None):  # type: ignore[override]
        last: Exception | None = None
        for i in range(ATTEMPTS):
            try:
                t = timeout
                if t is None or t > 30:
                    # getUpdates long-poll keeps its own timeout; others are short.
                    t = timeout if type(method).__name__ == "GetUpdates" else 15
                return await super().make_request(bot, method, timeout=t)
            except TelegramNetworkError as exc:
                last = exc
                logger.warning("Telegram %s retry %d: %s", type(method).__name__, i + 1, type(exc).__name__)
                await asyncio.sleep(0.4 * (i + 1))
        assert last is not None
        raise last
