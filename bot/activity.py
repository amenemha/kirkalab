"""Журнал действий: каждую команду и кнопку бот тихо отправляет в API (для админки).

Не блокирует ответ пользователю: отправка в фоне, ошибки глотаются. Текст,
который человек вводит (суммы, e-mail), не пишем — только факт «ввёл текст».
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings

log = logging.getLogger("kirkalab.bot.activity")
_tasks: set = set()


def _action(event) -> str | None:
    if isinstance(event, CallbackQuery):
        return (event.data or "")[:120] or None
    if isinstance(event, Message):
        t = (event.text or "").strip()
        if t.startswith("/"):
            return t.split()[0][:60] + ((" " + t.split()[1][:40]) if len(t.split()) > 1 else "")
        return "text" if t else None
    return None


async def _send(payload: dict) -> None:
    s = get_settings()
    if not s.bot_internal_secret:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(s.api_base_url.rstrip("/") + "/api/v1/internal/activity", json=payload,
                         headers={"X-Bot-Secret": s.bot_internal_secret})
    except Exception as exc:  # noqa: BLE001
        log.debug("activity send failed: %s", exc)


class ActivityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            u = getattr(event, "from_user", None)
            a = _action(event)
            if u and a and not getattr(u, "is_bot", False):
                name = " ".join(x for x in (u.first_name, u.last_name) if x) or None
                t = asyncio.create_task(_send({"telegram_user_id": u.id, "action": a, "username": u.username, "name": name}))
                _tasks.add(t)
                t.add_done_callback(_tasks.discard)
        except Exception:  # noqa: BLE001
            pass
        return await handler(event, data)
