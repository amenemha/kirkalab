"""/pools — pool totals for admins (all readable pools)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
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
    if ths >= 1 or ths == 0:
        return f"{_n(ths, 1)} TH/s"
    if ths >= 1e-3:
        return f"{_n(ths * 1e3, 1)} GH/s"
    return f"{_n(ths * 1e6, 0)} MH/s"


def _fmt(s: dict) -> str:
    from bot.handlers.hub import blocks, short_name

    when = datetime.fromisoformat(s["updated"]).astimezone(MSK).strftime("%H:%M") if s.get("updated") else "—"
    y = datetime.fromisoformat(s["yesterday"]).strftime("%d.%m")
    parts = []
    for c in s["coins"]:
        if not (c.get("ths_1h") or c.get("ths_1d") or c.get("yesterday") or c.get("w_active")):
            continue
        parts.append((f"{c['coin']} · {_hr(c['ths_1h'])}", [
            f"⏱ За час <b>{_hr(c['ths_1h'])}</b> · за сутки {_hr(c['ths_1d'])}",
            f"📅 {y}: в среднем {_hr(c['yesterday'])}",
            f"👷 Воркеров в работе {_n(c['w_active'])} · без хешрейта {_n(c['w_inactive'])}",
            f"🔗 Ссылок наблюдателя {_n(c['links'])}"]))
    if s["drops"]:
        parts.append((f"Просели за час · {s['drops_total']}", [
            f"📉 <b>{d['account']}</b> <i>{short_name(d['group'] or '—', 24)}</i>\n      {_hr(d['ths_1h'] or 0)} сейчас · {_hr(d['ths_1d'])} за сутки"
            for d in s["drops"]]))
    if s["broken"]:
        parts.append((f"Ссылки не читаются · {s['broken_total']}", [
            f"⚠️ {b['account'] or '—'} — {b.get('error') or ''} <i>{short_name(b['group'] or '—', 24)}</i>" for b in s["broken"]]))
    return blocks(f"🌊 <b>Пулы</b> · <i>{when} МСК</i>", parts)


def _kb() -> InlineKeyboardMarkup:
    st = get_settings()
    base = getattr(st, "web_app_url", None) or getattr(st, "site_url", None) or "https://kirkalab.ru"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔁 Обновить", callback_data="pools:refresh"),
        InlineKeyboardButton(text="🌊 Пулы на сайте", url=f"{base.rstrip('/')}/#pools"),
    ], [InlineKeyboardButton(text="☰ Меню", callback_data="hub:menu")]])


async def _load(api, uid: int) -> dict | None:
    st = get_settings()
    r = await api._request("GET", "/api/v1/internal/pools/summary", params={"telegram_user_id": uid},
                           headers={"X-Bot-Secret": st.bot_internal_secret or ""})
    return r.json() if r.status_code == 200 else None


@router.message(Command("pools", "puly"))
async def pools_cmd(message: Message, state: FSMContext) -> None:
    from bot.live_screen import drop_screen, edit_live_screen, safe_delete

    await safe_delete(message)
    await drop_screen(message, state)
    data = await _load(message.bot.kirkalab_client, message.from_user.id)
    text = _fmt(data) if data is not None else "🌊 Пулы пока доступны только администраторам."
    await edit_live_screen(message, state, text, reply_markup=_kb())


@router.callback_query(F.data == "pools:refresh")
async def pools_refresh(cb: CallbackQuery) -> None:
    data = await _load(cb.bot.kirkalab_client, cb.from_user.id)
    if data is None:
        await cb.answer("Пулы доступны только администраторам", show_alert=True)
        return
    try:
        await cb.message.edit_text(_fmt(data), reply_markup=_kb(), parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer("Обновлено")
