"""Отправка сообщений в Telegram: rich-сообщения (заголовки, таблицы) с запасным обычным HTML."""
from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger("kirka.tg")
API = "https://api.telegram.org/bot{}/{}"
_rich_ok = {"v": True}  # если Telegram отверг rich-формат, до перезапуска шлём обычным


def _token() -> str | None:
    from app.core.config import get_settings  # noqa: PLC0415
    return get_settings().bot_token


def call(method: str, payload: dict, timeout: float = 15) -> dict | None:
    tok = _token()
    if not tok:
        return None
    try:
        r = httpx.post(API.format(tok, method), json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        log.warning("telegram %s failed: %s", method, type(exc).__name__)
        return None
    try:
        j = r.json()
    except ValueError:
        j = {"ok": False}
    if not j.get("ok"):
        log.warning("telegram %s %s: %s", method, r.status_code, str(j.get("description"))[:200])
    return j


def plain(rich: str) -> str:
    """Грубый перевод rich-HTML в обычный Telegram-HTML (если rich недоступен)."""
    s = re.sub(r"<h\d[^>]*>(.*?)</h\d>", r"<b>\1</b>\n", rich, flags=re.S)
    s = re.sub(r"<tr[^>]*>(.*?)</tr>", lambda m: " · ".join(re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", m.group(1), re.S)) + "\n", s, flags=re.S)
    s = re.sub(r"<caption>(.*?)</caption>", r"<b>\1</b>\n", s, flags=re.S)
    s = re.sub(r"<footer>(.*?)</footer>", r"\n<i>\1</i>", s, flags=re.S)
    s = re.sub(r"</?(table|tbody|thead|p|details|summary|tg-button-row|hr)[^>]*>", "\n", s)
    s = re.sub(r"<tg-button[^>]*>.*?</tg-button>", "", s, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


_KEEP = object()


def send(chat_id: int, text: str | None, kb: list | None = None, *, rich: str | None = None, rich_kb=_KEEP,
         silent: bool = False) -> bool:
    """rich — тело для sendRichMessage; text — обычный вариант (если не задан, собирается из rich).
    rich_kb — клавиатура под rich-сообщением (по умолчанию та же kb; None — без клавиатуры)."""
    markup = {"inline_keyboard": kb} if kb else None
    if rich and _rich_ok["v"]:
        p = {"chat_id": chat_id, "rich_message": {"html": rich[:32000]}, "disable_notification": silent}
        rk = kb if rich_kb is _KEEP else rich_kb
        if rk:
            p["reply_markup"] = {"inline_keyboard": rk}
        j = call("sendRichMessage", p)
        if j and j.get("ok"):
            return True
        if j and j.get("error_code") == 404:  # метод неизвестен этому серверу Bot API
            _rich_ok["v"] = False
    body = text or plain(rich or "")
    p = {"chat_id": chat_id, "text": body[:4000], "parse_mode": "HTML", "disable_web_page_preview": True, "disable_notification": silent}
    if markup:
        p["reply_markup"] = markup
    j = call("sendMessage", p)
    return bool(j and j.get("ok"))
