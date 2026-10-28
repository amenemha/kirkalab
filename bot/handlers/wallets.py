"""👛 Кошельки: следить за поступлениями на свои адреса (TRC-20, BTC, ERC-20, BEP-20, TON, LTC, SOL, DOGE).

Адрес можно просто прислать в чат — бот узнает сеть и спросит, следить ли.
"""
from __future__ import annotations

import html
import re
from contextlib import suppress

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.live_screen import edit_live_screen, safe_delete

router = Router(name="wallets")
SITE = "https://kirkalab.ru/"
CAND = re.compile(r"(?<![\w/])[0-9A-Za-z:_-]{25,90}(?![\w/])")


class WalStates(StatesGroup):
    address = State()


def _b(text: str, cb: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb) if cb else InlineKeyboardButton(text=text, url=url)


async def call(bot, body: dict) -> dict | None:
    with suppress(Exception):
        r = await bot.kirkalab_client._request("POST", "/api/v1/internal/wallets",
                                               headers={"X-Bot-Secret": get_settings().bot_internal_secret or ""}, json=body)
        if r.status_code == 200:
            return r.json()
    return None


def _money(x: float, token: str) -> str:
    d = 2 if token in ("USDT", "USDC") or x >= 100 else (4 if x >= 1 else 8)
    s = f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    return s if token in ("USDT", "USDC") else s.rstrip("0").rstrip(",")


def wallet_line(w: dict) -> str:
    name = html.escape(w.get("label") or "Без названия")
    head = f"{'🔕' if w.get('muted') else '👛'} <b>{name}</b> · {w['net'] if w['chain'] in ('TRON', 'ETH', 'BSC') else w['chain_name']}"
    lines = [head, f"<code>{html.escape(w['address'])}</code>"]
    last = w.get("last")
    if w.get("error"):
        lines.append(f"⚠️ {html.escape(w['error'])}")
    elif last:
        lines.append(f"Последнее: +{_money(last['amount'], last['token'])} {html.escape(last['token'])} · {last['at'][8:10]}.{last['at'][5:7]}")
    else:
        lines.append("Поступлений пока не было")
    return "\n".join(lines)


def list_text(d: dict) -> str:
    items = d.get("items") or []
    lines = ["👛 <b>Кошельки</b>", ""]
    if not items:
        lines += ["Пришлите адрес кошелька — Kirka сама узнает сеть и будет сообщать о каждом поступлении.",
                  "", "Сети: TRON (TRC-20), Bitcoin, Ethereum (ERC-20), BNB Chain (BEP-20), TON, Litecoin, Solana, Dogecoin."]
    else:
        for w in items:
            lines += [wallet_line(w), ""]
        lines.append(f"Следим: {len(items)} из {d.get('limit')}. Спам, «пыль» меньше $1 и подменные адреса не присылаю.")
    return "\n".join(lines)


def list_kb(d: dict) -> InlineKeyboardMarkup:
    rows = []
    for w in (d.get("items") or [])[:10]:
        nm = (w.get("label") or w["address"][:6] + "…" + w["address"][-4:])[:18]
        rows.append([_b(("🔔 " if w.get("muted") else "🔕 ") + nm, f"wal:{'unmute' if w.get('muted') else 'mute'}:{w['id']}"),
                     _b("🗑", f"wal:del:{w['id']}")])
    rows.append([_b("➕ Добавить кошелёк", "wal:add"), _b("🌐 На сайте", url=f"{SITE}#wallets")])
    rows.append([_b("🔁 Обновить", "wal:open"), _b("☰ Меню", "hub:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show(message: Message, state: FSMContext, uid: int) -> None:
    d = await call(message.bot, {"action": "list", "tg_id": uid})
    text = list_text(d) if d else "Не получилось загрузить кошельки — нажмите /start и попробуйте ещё раз."
    await edit_live_screen(message, state, text, reply_markup=list_kb(d or {}))


@router.message(Command("wallets", "koshelki"))
async def cmd_wallets(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    await state.set_state(None)
    await show(message, state, message.from_user.id)


@router.callback_query(F.data == "wal:open")
async def cb_open(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await cb.answer()
    await show(cb.message, state, cb.from_user.id)


@router.callback_query(F.data == "wal:add")
async def cb_add(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WalStates.address)
    await cb.answer()
    await edit_live_screen(cb.message, state, "👛 Пришлите адрес кошелька одним сообщением.\n\nМожно добавить название через пробел: <code>адрес Личный</code>",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("✖️ Отмена", "wal:open")]]))


def _ask_kb(addr_key: str, chains: list[dict]) -> InlineKeyboardMarkup:
    if len(chains) > 1:
        rows = [[_b("✅ Да, " + " + ".join(c["name"] for c in chains), f"wal:yes:{addr_key}:*")],
                [_b(f"Только {c['name']}", f"wal:yes:{addr_key}:{c['id']}") for c in chains]]
    else:
        rows = [[_b("✅ Да, следить", f"wal:yes:{addr_key}:*")]]
    rows.append([_b("Нет", "wal:no")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ask(message: Message, state: FSMContext, text: str) -> bool:
    d = await call(message.bot, {"action": "detect", "text": text})
    if not d or not d.get("address"):
        return False
    addr, chains = d["address"], d["chains"]
    label = text.replace(addr, " ").strip()[:60] or None
    await state.update_data(wal_addr=addr, wal_label=label)
    nets = " / ".join(f"{c['name']} ({c['net']})" if c["net"] != c["id"] else c["name"] for c in chains)
    await message.answer(f"👛 Это адрес <b>{html.escape(nets)}</b>\n<code>{html.escape(addr)}</code>\n\nСледить за поступлениями?",
                         reply_markup=_ask_kb("s", chains))
    return True


@router.message(WalStates.address, F.text & ~F.text.startswith("/"))
async def got_address(message: Message, state: FSMContext) -> None:
    await state.set_state(None)
    if not await ask(message, state, message.text):
        await message.answer("Не узнаю адрес. Поддерживаются TRON, Bitcoin, Ethereum, BNB Chain, TON, Litecoin, Solana, Dogecoin.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("➕ Ещё раз", "wal:add"), _b("👛 Кошельки", "wal:open")]]))


@router.message(StateFilter(None), F.text & ~F.text.startswith("/") & ~F.text.regexp(r"https?://"))
async def pasted_address(message: Message, state: FSMContext) -> None:
    """Адрес кошелька, присланный просто в чат; остальной текст — дальше, менеджеру."""
    t = message.text or ""
    if len(t) > 200 or not CAND.search(t) or not await ask(message, state, t):
        raise SkipHandler


@router.callback_query(F.data.startswith("wal:yes:"))
async def cb_yes(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    addr = data.get("wal_addr")
    if not addr:
        await cb.answer("Пришлите адрес ещё раз", show_alert=True)
        return
    ch = cb.data.split(":")[3]
    r = await call(cb.bot, {"action": "add", "tg_id": cb.from_user.id, "address": addr, "label": data.get("wal_label"),
                            "chains": None if ch == "*" else [ch]})
    await cb.answer()
    if not r or not r.get("ok"):
        await cb.message.edit_text("⚠️ " + html.escape((r or {}).get("error") or "Не получилось связаться с Kirka"),
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("👛 Кошельки", "wal:open")]]))
        return
    nets = ", ".join(w["net"] if w["chain"] in ("TRON", "ETH", "BSC") else w["chain_name"] for w in r["added"])
    err = next((w["error"] for w in r["added"] if w.get("error")), None)
    txt = f"✅ Слежу за кошельком ({nets}).\n<code>{html.escape(addr)}</code>\n\nО каждом поступлении напишу сюда."
    if err:
        txt += f"\n\n⚠️ Сеть пока не ответила ({html.escape(err)}) — попробую снова через пару минут."
    await cb.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b("👛 Кошельки", "wal:open"), _b("☰ Меню", "hub:menu")]]))


@router.callback_query(F.data == "wal:no")
async def cb_no(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer("Хорошо, не слежу")
    with suppress(Exception):
        await cb.message.delete()


@router.callback_query(F.data.regexp(r"^wal:(mute|unmute|del):\d+$"))
async def cb_mute(cb: CallbackQuery, state: FSMContext) -> None:
    _, act, wid = cb.data.split(":")
    r = await call(cb.bot, {"action": {"del": "delete"}.get(act, act), "tg_id": cb.from_user.id, "id": int(wid)})
    ok = bool(r and r.get("ok"))
    await cb.answer({"mute": "Уведомления по кошельку выключены", "unmute": "Уведомления включены", "del": "Кошелёк удалён"}[act]
                    if ok else "Кошелёк не найден")
    if cb.message and cb.message.text and cb.message.text.startswith("👛 Кошельки"):
        await show(cb.message, state, cb.from_user.id)
