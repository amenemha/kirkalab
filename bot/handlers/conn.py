"""🔌 Подключения: свой CROFT (логин/пароль, только чтение) и ссылки наблюдателя пулов.

Ссылку пула можно просто прислать в чат — бот узнает её и подключит сам.
"""
from __future__ import annotations

import html
from contextlib import suppress

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.live_screen import edit_live_screen, safe_delete

router = Router(name="conn")


class ConnStates(StatesGroup):
    login = State()
    password = State()
    pool = State()


def _b(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def _hdr() -> dict:
    return {"X-Bot-Secret": get_settings().bot_internal_secret or ""}


async def call(bot, body: dict) -> dict | None:
    with suppress(Exception):
        r = await bot.kirkalab_client._request("POST", "/api/v1/internal/connections", headers=_hdr(), json=body)
        if r.status_code == 200:
            return r.json()
    return None


def _n(x) -> str:
    return f"{int(x or 0):,}".replace(",", " ")


def croft_line(c: dict) -> str:
    st = c.get("stats") or {}
    if c.get("status") != "ok":
        return f"🔴 CROFT <b>{html.escape(c['login'])}</b> — {html.escape(c.get('error') or 'нет доступа')}"
    s = (f"🟢 CROFT <b>{html.escape(c['login'])}</b> — {_n(st.get('devices'))} асиков, "
         f"в работе {_n(st.get('working'))}, не на связи {_n(st.get('offline'))}")
    if st.get("hashrate_ths"):
        s += f", {st['hashrate_ths']:g} TH/s"
    return s


def pool_line(p: dict) -> str:
    coin = f" {p['coin']}" if p.get("coin") else ""
    if not p.get("live"):
        return f"🔗 {p['name']}{coin} — ссылка принята, чтение этого пула скоро добавлю"
    if p.get("hs_1h") is not None:
        w = p.get("workers_active")
        return f"🟢 {p['name']}{coin} — {p['hs_1h'] / 1e12:.1f} TH/s" + (f", воркеров {w}" if w is not None else "")
    return f"🟡 {p['name']}{coin} — ждёт первого обновления"


def conn_text(d: dict) -> str:
    lines = ["🔌 <b>Подключения</b>", ""]
    srv = d.get("server") or {}
    if srv.get("croft"):
        lines.append(f"🖥 Общая ферма (сервер): CROFT <b>{html.escape(srv['croft']['login'])}</b> — "
                     f"{_n(srv['croft'].get('devices'))} асиков. Видна вам как администратору.")
        if srv.get("pools"):
            lines.append(f"🌊 Общие ссылки пулов: {_n(srv['pools'])}")
        lines.append("")
    crofts, pools = d.get("croft") or [], d.get("pools") or []
    lines += [croft_line(c) for c in crofts] or ["CROFT: свой ещё не подключён."]
    lines += [pool_line(p) for p in pools] or ["Пулы: своих ссылок наблюдателя пока нет."]
    lines += ["", "Доступ только на чтение: Kirka ничего не меняет в CROFT и на пулах.",
              "Ссылку наблюдателя можно просто прислать в чат."]
    return "\n".join(lines)


def conn_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_b("🔑 Подключить CROFT", "conn:croft"), _b("🔗 Ссылка пула", "conn:pool")],
        [_b("🔁 Обновить", "conn:open"), _b("☰ Меню", "hub:menu")],
    ])


async def show_conn(message: Message, state: FSMContext, uid: int) -> None:
    d = await call(message.bot, {"action": "list", "tg_id": uid})
    text = conn_text(d) if d else "Не получилось загрузить подключения — нажмите /start и попробуйте ещё раз."
    await edit_live_screen(message, state, text, reply_markup=conn_kb())


@router.message(Command("connect", "podkluchenia"))
async def cmd_conn(message: Message, state: FSMContext) -> None:
    await safe_delete(message)
    await state.set_state(None)
    await show_conn(message, state, message.from_user.id)


@router.callback_query(F.data == "conn:open")
async def cb_open(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await cb.answer()
    await show_conn(cb.message, state, cb.from_user.id)


CANCEL = InlineKeyboardMarkup(inline_keyboard=[[_b("✖️ Отмена", "conn:open")]])


@router.callback_query(F.data == "conn:croft")
async def cb_croft(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ConnStates.login)
    await cb.answer()
    await edit_live_screen(cb.message, state, "🔑 <b>CROFT</b>\n\nПришлите логин, которым вы входите в CROFT.",
                           reply_markup=CANCEL)


@router.message(ConnStates.login, F.text & ~F.text.startswith("/"))
async def got_login(message: Message, state: FSMContext) -> None:
    await state.update_data(croft_login=message.text.strip())
    await safe_delete(message)
    await state.set_state(ConnStates.password)
    await edit_live_screen(message, state, f"🔑 <b>CROFT</b> · {html.escape(message.text.strip())}\n\n"
                           "Теперь пароль. Сообщение с паролем я сразу удалю из чата.", reply_markup=CANCEL)


@router.message(ConnStates.password, F.text & ~F.text.startswith("/"))
async def got_password(message: Message, state: FSMContext) -> None:
    pw = message.text.strip()
    await safe_delete(message)
    login = (await state.get_data()).get("croft_login") or ""
    await state.set_state(None)
    await edit_live_screen(message, state, "⏳ Проверяю вход в CROFT…")
    r = await call(message.bot, {"action": "croft", "tg_id": message.from_user.id, "login": login, "password": pw})
    if not r:
        text = "Не получилось связаться с Kirka — попробуйте ещё раз."
    elif not r.get("ok"):
        text = f"🔴 {html.escape(r.get('error') or 'Не получилось')}\n\nПроверьте логин и пароль и попробуйте снова."
    else:
        text = f"✅ <b>CROFT подключён</b>\n\n{html.escape(r['text'])}"
    await edit_live_screen(message, state, text, reply_markup=conn_kb())


@router.callback_query(F.data == "conn:pool")
async def cb_pool(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ConnStates.pool)
    await cb.answer()
    await edit_live_screen(cb.message, state,
                           "🔗 <b>Ссылка наблюдателя</b>\n\nПришлите ссылку наблюдателя (Observer / Watcher) из кабинета пула: "
                           "ViaBTC, Antpool, F2Pool, EMCD, NiceHash и другие.", reply_markup=CANCEL)


async def _add_pool(message: Message, state: FSMContext, r: dict) -> None:
    if not r.get("ok"):
        text = f"🔴 {html.escape(r.get('error') or 'Не получилось')}"
    else:
        a = r.get("account") or {}
        head = "Эта ссылка уже подключена." if r.get("already") else "✅ <b>Ссылка подключена</b>"
        text = f"{head}\n\n{pool_line(a)}"
    await edit_live_screen(message, state, text, reply_markup=conn_kb())


@router.message(ConnStates.pool, F.text & ~F.text.startswith("/"))
async def got_pool(message: Message, state: FSMContext) -> None:
    await state.set_state(None)
    r = await call(message.bot, {"action": "pool", "tg_id": message.from_user.id, "text": message.text})
    await _add_pool(message, state, r or {"ok": False, "error": "Не получилось связаться с Kirka"})


@router.message(StateFilter(None), F.text.regexp(r"https?://"))
async def pasted_link(message: Message, state: FSMContext) -> None:
    """Ссылку пула, присланную просто в чат, подключаем; остальное — дальше, менеджеру."""
    r = await call(message.bot, {"action": "pool", "tg_id": message.from_user.id, "text": message.text})
    if not r or r.get("unknown"):
        raise SkipHandler
    await _add_pool(message, state, r)
