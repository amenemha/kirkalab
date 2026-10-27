"""Обходчик бота: нажимает каждую кнопку и проверяет, что она что-то делает.

Запускается внутри контейнера бота (реальный backend, без отправки в Telegram):

    docker exec -i kirkalab-bot-1 python - < tools/bot_crawl.py

Для каждой кнопки: заново проходит путь от /start, нажимает кнопку и проверяет —
обработчик есть, исключений нет, пользователь видит результат (новое/изменённое
сообщение или всплывающий ответ), в текстах нет запрещённых слов.
"""
from __future__ import annotations

import asyncio
import re
import sys
import traceback
from collections import Counter
from datetime import datetime

sys.path.insert(0, __import__("os").getenv("BOTPATH", "/app"))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.dispatcher.event.bases import UNHANDLED  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    Update,
    User,
)

from bot.api_client import KirkalabApiClient  # noqa: E402
from bot.config import get_settings  # noqa: E402
from bot.handlers import routers  # noqa: E402

import os
DUMP = os.getenv("DUMP") == "1"
UID = 183868623
FORBIDDEN = re.compile(r"купить|оплат|тариф|цен[аыуе]\b|скидк", re.I)
VISIBLE = {"SendMessage", "EditMessageText", "EditMessageReplyMarkup", "SendDocument",
           "SendInvoice", "SendPhoto", "EditMessageCaption", "EditMessageMedia"}
USER = User(id=UID, is_bot=False, first_name="Тест")
CHAT = Chat(id=UID, type="private")
BOTUSER = User(id=8928450746, is_bot=True, first_name="Кирка", username="kirkalabbot")


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.mid = 1000

    async def close(self) -> None:  # noqa: D401
        return None

    async def stream_content(self, *a, **k):  # pragma: no cover
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method))
        if name == "GetMe":
            return BOTUSER
        if name in VISIBLE:
            mid = getattr(method, "message_id", None)
            if not mid:
                self.mid += 1
                mid = self.mid
            rm = getattr(method, "reply_markup", None)
            return Message(message_id=mid, date=datetime.now(), chat=CHAT, from_user=BOTUSER,
                           text=getattr(method, "text", None) or getattr(method, "caption", None) or "·",
                           reply_markup=rm if isinstance(rm, InlineKeyboardMarkup) else None)
        return True


def buttons(calls):
    """Inline-кнопки, reply-кнопки, url-кнопки и все тексты из исходящих вызовов."""
    inl, rep, urls, texts = [], [], [], []
    for name, m in calls:
        for attr in ("text", "caption"):
            v = getattr(m, attr, None)
            if isinstance(v, str):
                texts.append(v)
        if name == "AnswerCallbackQuery" and getattr(m, "text", None):
            texts.append(m.text)
        rm = getattr(m, "reply_markup", None)
        if isinstance(rm, InlineKeyboardMarkup):
            for row in rm.inline_keyboard:
                for b in row:
                    texts.append(b.text)
                    if b.callback_data and b.callback_data != "notif:test":  # проверка шлёт реальное сообщение
                        inl.append((b.text, b.callback_data))
                    if b.url:
                        urls.append((b.text, b.url))
        if isinstance(rm, ReplyKeyboardMarkup):
            for row in rm.keyboard:
                for b in row:
                    texts.append(b.text)
                    rep.append(b.text)
    return inl, rep, urls, texts


class Crawler:
    def __init__(self) -> None:
        st = get_settings()
        self.sess = FakeSession()
        self.bot = Bot(token=st.bot_token, session=self.sess, default=DefaultBotProperties(parse_mode="HTML"))
        self.bot.kirkalab_client = KirkalabApiClient(base_url=st.api_base_url, timeout=20)
        self.bot.kirkalab_notifier = None
        self.n = 0
        self.errors: list[str] = []
        self.fails: list[str] = []
        self.dp = None

    def fresh(self) -> None:
        dp = Dispatcher(storage=MemoryStorage())
        for r in routers:
            if r.parent_router is not None:
                r._parent_router = None  # allow re-including in a fresh dispatcher
            dp.include_router(r)

        @dp.errors()
        async def _err(ev):  # noqa: ANN001
            tb = traceback.extract_tb(ev.exception.__traceback__)
            where = next((f"{f.filename.split('/app/')[-1]}:{f.lineno}" for f in reversed(tb) if "/bot/" in f.filename and "site-packages" not in f.filename), "?")
            self.errors.append(f"{type(ev.exception).__name__}: {str(ev.exception)[:160]} ({where})")
            return True

        self.dp = dp

    async def send_text(self, text: str):
        self.n += 1
        msg = Message(message_id=self.n, date=datetime.now(), chat=CHAT, from_user=USER, text=text)
        return await self.dp.feed_update(self.bot, Update(update_id=self.n, message=msg))

    async def press(self, data: str, mid: int):
        self.n += 1
        cq = CallbackQuery(id=str(self.n), from_user=USER, chat_instance="1", data=data,
                           message=Message(message_id=mid, date=datetime.now(), chat=CHAT, from_user=BOTUSER, text="·"))
        return await self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=cq))

    async def step(self, kind: str, val: str):
        """Выполнить шаг и вернуть (handled, calls, error_count_before)."""
        before = len(self.errors)
        self.sess.calls = []
        last_mid = self.sess.mid
        r = await (self.send_text(val) if kind == "text" else self.press(val, last_mid))
        return r is not UNHANDLED, list(self.sess.calls), before

    async def replay(self, path):
        self.fresh()
        for kind, val in path:
            await self.step(kind, val)


def family(data: str) -> str:
    parts = data.split(":")
    return ":".join(parts[:2]) if len(parts) > 2 else data


async def main() -> None:
    c = Crawler()
    seen: set[str] = set()
    fam = Counter()
    queue: list[tuple[list, str, str, str]] = []  # (path, kind, value, label)
    starts = ["/start", "/menu", "/ferma", "/problems", "/pools", "/site", "/help"]
    for s in starts:
        queue.append(([], "text", s, s))
        seen.add("text:" + s)
    tested = 0
    forb = set()
    urls_all = set()
    while queue and tested < 400:
        path, kind, val, label = queue.pop(0)
        await c.replay(path)
        ok, calls, eb = await c.step(kind, val)
        tested += 1
        names = [n for n, _ in calls]
        vis = [n for n in names if n in VISIBLE] or [n for n, m in calls if n == "AnswerCallbackQuery" and getattr(m, "text", None)]
        where = " → ".join([v for _, v in path] + [label])
        if not ok:
            c.fails.append(f"не обработано: {where}")
        elif len(c.errors) > eb:
            c.fails.append(f"ошибка: {where}: {c.errors[-1]}")
        elif not vis and not val.endswith(":noop"):
            c.fails.append(f"нажатие без видимого результата: {where} (вызовы: {names})")
        inl, rep, urls, texts = buttons(calls)
        if DUMP and len(path) <= 1:
            for n, m in calls:
                if n in VISIBLE and getattr(m, "text", None):
                    kb = getattr(m, "reply_markup", None)
                    rows = [" | ".join(b.text for b in r) for r in kb.inline_keyboard] if isinstance(kb, InlineKeyboardMarkup) else []
                    print(f"\n=== {where} [{n}]\n{m.text[:700]}\n  " + "\n  ".join(rows))
        for t in texts:
            m = FORBIDDEN.search(t or "")
            if m:
                forb.add(f"«{m.group(0)}» в: {t[:80]!r} ({where})")
        for t, u in urls:
            urls_all.add((t, u.split("#")[0][:60]))
        newpath = path + [(kind, val)]
        for t, d in inl:
            if d in seen:
                continue
            f = family(d)
            if fam[f] >= 3:
                continue
            fam[f] += 1
            seen.add(d)
            queue.append((newpath, "cb", d, f"[{t}]"))
        for t in rep:
            if "text:" + t not in seen:
                seen.add("text:" + t)
                queue.append((newpath, "text", t, f"<{t}>"))
    print(f"Нажато кнопок/команд: {tested}")
    print(f"Ссылки: {sorted(urls_all)}")
    for f in forb:
        c.fails.append("запрещённое слово " + f)
    if c.fails:
        print(f"ПРОБЛЕМ: {len(c.fails)}")
        for f in c.fails:
            print(" ✗", f)
    else:
        print("ВСЁ ОК: каждая кнопка отвечает, ошибок нет")
    await c.bot.session.close()
    if c.fails:
        sys.exit(1)


asyncio.run(main())
