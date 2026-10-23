"""/ferma — farm summary for admins (CROFT data)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from bot.api_client import KirkalabApiClient as ApiClient
from bot.config import get_settings

router = Router(name="farm")
MSK = timezone(timedelta(hours=3))


def _n(x: float | int | None, d: int = 0) -> str:
    if x is None:
        return "—"
    s = f"{x:,.{d}f}".replace(",", " ")
    return s.replace(".", ",")


def _fmt(s: dict) -> str:
    when = ""
    if s.get("synced_at"):
        when = datetime.fromisoformat(s["synced_at"]).astimezone(MSK).strftime("%H:%M")
    ph = (s.get("ths") or 0) / 1000
    lines = [
        f"<b>Ферма · сейчас</b>  <i>{when} МСК</i>",
        "",
        f"Работают <b>{_n(s['working'])}</b> из {_n(s['total'])} · офлайн {_n(s['offline'])} · пауза {_n(s['suspended'])}",
        f"Хешрейт <b>{_n(ph, 2)} PH/s</b> · {_n(s.get('efficiency'), 1)}% от паспорта",
        f"Мощность {_n((s.get('kw') or 0) / 1000, 2)} МВт",
        f"С ошибками {_n(s['with_alarms'])} асиков",
    ]
    inc = [i for i in s.get("incidents") or [] if not i.get("ended")]
    if inc:
        lines += ["", "<b>Аварии сейчас</b>"] + [f"• {i['scope']}: {i['down']} из {i['total']} офлайн" for i in inc[:5]]
    cls = [c for c in s.get("classes") or [] if c["count"]]
    if cls:
        lines += ["", "<b>Ошибки по классам</b>"] + [f"• {c['label']} — {_n(c['count'])}" for c in cls]
    worst = sorted((c for c in s.get("containers") or [] if c["total"]), key=lambda c: c["offline"] / c["total"], reverse=True)[:5]
    if worst:
        lines += ["", "<b>Контейнеры с офлайном</b>"] + [f"• {c['name'][:40]} — {c['offline']}/{c['total']}" for c in worst if c["offline"]]
    return "\n".join(lines)


def _kb(url: str | None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Обновить", callback_data="farm:refresh")]]
    if url:
        rows[0].append(InlineKeyboardButton(text="Панель на сайте", url=url))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _load(api: ApiClient, uid: int) -> dict | None:
    st = get_settings()
    r = await api._request("GET", "/api/v1/internal/farm/summary", params={"telegram_user_id": uid},
                           headers={"X-Bot-Secret": st.bot_internal_secret or ""})
    if r.status_code != 200:
        return None
    return r.json()


def _site_url() -> str | None:
    st = get_settings()
    base = getattr(st, "web_app_url", None) or getattr(st, "site_url", None)
    return f"{base.rstrip('/')}/farm" if base else "https://kirkalab.ru/farm"


@router.message(Command("ferma", "farm"))
async def farm_cmd(message: Message) -> None:
    api = message.bot.kirkalab_client
    data = await _load(api, message.from_user.id)
    if data is None:
        await message.answer("Ферма пока доступна только администраторам.")
        return
    await message.answer(_fmt(data), reply_markup=_kb(_site_url()), parse_mode="HTML")


@router.callback_query(F.data == "farm:refresh")
async def farm_refresh(cb: CallbackQuery) -> None:
    api = cb.bot.kirkalab_client
    data = await _load(api, cb.from_user.id)
    if data is None:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        await cb.message.edit_text(_fmt(data), reply_markup=_kb(_site_url()), parse_mode="HTML")
    except Exception:  # noqa: BLE001 — "message is not modified"
        pass
    await cb.answer("Обновлено")
