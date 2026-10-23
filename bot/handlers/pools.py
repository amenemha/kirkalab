"""/pools — pool totals for admins (ViaBTC archive)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.handlers.farm import _n

router = Router(name="pools")
MSK = timezone(timedelta(hours=3))


def _hr(ths: float) -> str:
    if ths >= 1e6:
        return f"{_n(ths / 1e6, 2)} EH/s"
    if ths >= 1e3:
        return f"{_n(ths / 1e3, 2)} PH/s"
    return f"{_n(ths, 1)} TH/s"


def _fmt(s: dict) -> str:
    when = datetime.fromisoformat(s["updated"]).astimezone(MSK).strftime("%H:%M") if s.get("updated") else "—"
    y = datetime.fromisoformat(s["yesterday"]).strftime("%d.%m")
    lines = [f"<b>Пулы · сейчас</b>  <i>{when} МСК</i>", ""]
    for c in s["coins"]:
        lines.append(f"<b>{c['coin']}</b> · {_hr(c['ths_1h'])} за час · {_hr(c['ths_1d'])} за сутки")
        lines.append(f"   воркеров {_n(c['w_active'])} в работе, {_n(c['w_inactive'])} без хешрейта · ссылок {c['links']}")
        lines.append(f"   {y}: в среднем {_hr(c['yesterday'])}")
    if s["drops"]:
        lines += ["", f"<b>Просели за час</b> ({s['drops_total']})"]
        lines += [f"• {d['account']} ({d['group'] or '—'}): {_hr(d['ths_1h'] or 0)} при {_hr(d['ths_1d'])} за сутки" for d in s["drops"]]
    if s["broken"]:
        lines += ["", f"<b>Ссылки не читаются</b> ({s['broken_total']})"]
        lines += [f"• {b['account'] or '—'} ({b['group'] or '—'})" for b in s["broken"]]
    return "\n".join(lines)


def _kb() -> InlineKeyboardMarkup:
    st = get_settings()
    base = getattr(st, "web_app_url", None) or getattr(st, "site_url", None) or "https://kirkalab.ru"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Обновить", callback_data="pools:refresh"),
        InlineKeyboardButton(text="Пулы на сайте", url=f"{base.rstrip('/')}/farm#pools"),
    ]])


async def _load(api, uid: int) -> dict | None:
    st = get_settings()
    r = await api._request("GET", "/api/v1/internal/pools/summary", params={"telegram_user_id": uid},
                           headers={"X-Bot-Secret": st.bot_internal_secret or ""})
    return r.json() if r.status_code == 200 else None


@router.message(Command("pools", "puly"))
async def pools_cmd(message: Message) -> None:
    data = await _load(message.bot.kirkalab_client, message.from_user.id)
    if data is None:
        await message.answer("Пулы пока доступны только администраторам.")
        return
    await message.answer(_fmt(data), reply_markup=_kb(), parse_mode="HTML")


@router.callback_query(F.data == "pools:refresh")
async def pools_refresh(cb: CallbackQuery) -> None:
    data = await _load(cb.bot.kirkalab_client, cb.from_user.id)
    if data is None:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        await cb.message.edit_text(_fmt(data), reply_markup=_kb(), parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer("Обновлено")
