"""Hub: Crypto-Office-style inline main menu, farm card, problems, one-tap site login.

Every screen is a single "live" message edited in place; the user's own
command/button messages are deleted so the chat stays clean.
"""
from __future__ import annotations

from contextlib import suppress

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.handlers.farm import _load as farm_load
from bot.live_screen import drop_screen, edit_live_screen, safe_delete, set_screen_id

router = Router(name="hub")
SITE = "https://kirkalab.ru/"

HUB_TEXT = (
    "🏠 <b>Kirka</b> — ферма, пулы и расчёты в одном месте.\n\n"
    "Выберите раздел. Сайт открывается кнопкой <b>«Открыть Kirka»</b> — "
    "уже с входом, без QR."
)
HELP_TEXT = (
    "ℹ️ <b>Как пользоваться</b>\n\n"
    "🏭 <b>Ферма</b> — сколько асиков в работе, хешрейт, мощность, аварии.\n"
    "⚠️ <b>Проблемы</b> — ошибки по классам и контейнеры, где больше всего офлайна.\n"
    "🌊 <b>Пулы</b> — хешрейт по пулам и монетам за сутки.\n"
    "🧮 <b>Расчёт</b> — доходность и окупаемость асика.\n"
    "🔌 <b>Подключения</b> — свой CROFT и ссылки наблюдателя пулов (ссылку можно просто прислать в чат).\n"
    "🌐 <b>Открыть Kirka</b> — сайт с картой контейнеров, стеллажами и полками.\n\n"
    "Команды: /menu, /ferma, /problems, /pools, /connect, /site, /help"
)


def _n(x, d: int = 0) -> str:
    if x is None:
        return "—"
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def short_name(name: str, limit: int = 30) -> str:
    name = (name or "—").strip()
    if len(name) <= limit:
        return name
    cut = name[:limit].rsplit(" ", 1)[0]
    return cut + "…"


NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]


def blocks(title: str, parts: list[tuple[str, list[str]]], foot: str | None = None) -> str:
    """Сообщение блоками: заголовок, нумерованные разделы, внутри — цитата.
    Длинные списки сворачиваются (expandable), чтобы экран не превращался в простыню."""
    out = [title]
    i = 0
    for head, rows in parts:
        rows = [r for r in rows if r]
        if not rows:
            continue
        body = "\n".join(rows)
        tag = "blockquote expandable" if len(rows) > 4 else "blockquote"
        out += ["", f"{NUM[i] if i < len(NUM) else '▪️'} <b>{head}</b>", f"<{tag}>{body}</blockquote>"]
        i += 1
    if foot:
        out += ["", f"<i>{foot}</i>"]
    return "\n".join(out)


def _when(iso: str | None) -> str:
    from datetime import datetime, timedelta, timezone

    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=3))).strftime("%H:%M")


def farm_text(s: dict) -> str:
    total, work, off = s.get("total") or 0, s.get("working") or 0, s.get("offline") or 0
    susp, al = s.get("suspended") or 0, s.get("with_alarms") or 0
    head = "🟢" if not off else ("🔴" if off > total * 0.05 else "🟡")
    pct = work / total * 100 if total else 0
    now = [f"{head} В работе <b>{_n(work)}</b> из {_n(total)} · {_n(pct)}%",
           f"⛏ Хешрейт <b>{_n((s.get('ths') or 0) / 1000, 2)} PH/s</b>"
           + (f" · {_n(s.get('efficiency'))}% от заводского" if s.get("efficiency") else ""),
           f"⚡ Потребление <b>{_n((s.get('kw') or 0) / 1000, 2)} МВт</b>"]
    idle = [f"🔴 Нет связи — <b>{_n(off)}</b>" if off else "✅ Все асики на связи",
            f"💤 Сон — {_n(susp)}" if susp else "",
            f"🟡 С ошибками — {_n(al)} {plural(al, 'асик', 'асика', 'асиков')}" if al else ""]
    inc = [i for i in s.get("incidents") or [] if not i.get("ended")]
    sites = [x for x in s.get("sites") or [] if x.get("total")]
    parts = [("Сейчас", now), ("Не в работе", idle),
             ("Аварии", [f"🚨 {short_name(i['scope'])} — {_n(i['down'])} из {_n(i['total'])}" for i in inc[:8]])]
    if len(sites) > 1:
        parts.append(("Площадки", [f"🏗 {short_name(x['name'])} — {_n(x.get('working'))} из {_n(x['total'])}"
                                   f" · {_n((x.get('working') or 0) / x['total'] * 100)}%" for x in sites]))
    return blocks(f"🏭 <b>Ферма</b> · <i>{_when(s.get('synced_at'))} МСК</i>", parts, "Ошибки по типам — в «Проблемах».")


def btn(text: str, cb: str | None = None, url: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    kw = {"text": text}
    if cb:
        kw["callback_data"] = cb
    if url:
        kw["url"] = url
    if style:
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except Exception:  # noqa: BLE001 — older aiogram without button styles
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)


async def site_link(bot, uid: int, page: str = "today") -> str:
    """One-time pre-approved login link; falls back to the plain site."""
    st = get_settings()
    with suppress(Exception):
        r = await bot.kirkalab_client._request(
            "POST", "/api/v1/auth/qr/bot-link", json={"telegram_user_id": uid},
            headers={"X-Bot-Secret": st.bot_internal_secret or ""})
        if r.status_code == 200:
            return f"{SITE}#s={r.json()['session_id']}&p={page}"
    return f"{SITE}#{page}"


def hub_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🏭 Ферма", "hub:farm"), btn("⚠️ Проблемы", "hub:problems")],
        [btn("🌊 Пулы", "pools:refresh"), btn("🔔 Уведомления", "notif:open")],
        [btn("🧮 Расчёт", "menu:calculator"), btn("📋 Каталог ASIC", "menu:catalog")],
        [btn("📊 Мои отчёты", "menu:reports"), btn("👤 Профиль", "menu:profile")],
        [btn("🔌 Подключения", "conn:open"), btn("✉️ Написать менеджеру", "sup:write")],
        [btn("🌐 Открыть Kirka", url=url, style="success"), btn("❓ Помощь", "hub:help")],
    ])


def farm_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🔁 Обновить", "hub:farm:r"), btn("⚠️ Проблемы", "hub:problems")],
        [btn("🔔 Уведомления", "notif:open"), btn("📊 Открыть на сайте", url=url, style="primary")],
        [btn("☰ Меню", "hub:menu")],
    ])


def problems_text(s: dict) -> str:
    total, off, al = s.get("total") or 0, s.get("offline") or 0, s.get("with_alarms") or 0
    inc = [i for i in s.get("incidents") or [] if not i.get("ended")]
    cls = sorted((c for c in s.get("classes") or [] if c.get("count")), key=lambda c: -c["count"])
    worst = sorted((c for c in s.get("containers") or [] if c.get("total") and c.get("offline")),
                   key=lambda c: c["offline"] / c["total"], reverse=True)[:8]
    parts = [("Сводка", [f"🔴 Не работают — <b>{_n(off)}</b> из {_n(total)}",
                         f"🟡 С ошибками — <b>{_n(al)}</b> {plural(al, 'асик', 'асика', 'асиков')}"]),
             ("Аварии", [f"🚨 {short_name(i['scope'])} — {_n(i['down'])} из {_n(i['total'])}" for i in inc[:8]]),
             ("Ошибки по типу", [f"• {c['label']} — <b>{_n(c['count'])}</b>" for c in cls]),
             ("Где больше всего не работает", [f"• {short_name(c['name'])} — {_n(c['offline'])} из {_n(c['total'])}" for c in worst])]
    foot = "✅ Всё спокойно." if not (off or al or inc) else None
    return blocks("⚠️ <b>Проблемы сейчас</b>", parts, foot)


def problems_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🔁 Обновить", "hub:problems:r"), btn("🏭 Ферма", "hub:farm")],
        [btn("📋 Все проблемы на сайте", url=url, style="danger")],
        [btn("☰ Меню", "hub:menu")],
    ])


async def show_hub(message: Message, state: FSMContext, uid: int) -> None:
    await edit_live_screen(message, state, HUB_TEXT, reply_markup=hub_kb(await site_link(message.bot, uid)))


async def show_farm(message: Message, state: FSMContext, uid: int) -> None:
    data = await farm_load(message.bot.kirkalab_client, uid)
    if data is None:
        from bot.handlers.conn import call, croft_line  # noqa: PLC0415
        d = await call(message.bot, {"action": "list", "tg_id": uid}) or {}
        crofts = d.get("croft") or []
        if crofts:
            text = "🏭 <b>Ваша ферма</b>\n\n" + "\n".join(croft_line(c) for c in crofts)
            for c in crofts:
                for s in ((c.get("stats") or {}).get("sites") or [])[:8]:
                    text += f"\n• {s['name']} — {s['devices']} асиков, в работе {s['working']}"
        else:
            text = ("🏭 <b>Ферма</b>\n\nПодключите свой CROFT — я покажу асики, площадки и хешрейт. "
                    "Нужны логин и пароль CROFT, доступ только на чтение.")
        await edit_live_screen(message, state, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("🔌 Подключения", "conn:open")], [btn("☰ Меню", "hub:menu")]]))
        return
    await edit_live_screen(message, state, farm_text(data),
                           reply_markup=farm_kb(await site_link(message.bot, uid, "map")))


async def show_problems(message: Message, state: FSMContext, uid: int) -> None:
    data = await farm_load(message.bot.kirkalab_client, uid)
    if data is None:
        await edit_live_screen(message, state, "⚠️ Раздел доступен только администраторам.",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("☰ Меню", "hub:menu")]]))
        return
    await edit_live_screen(message, state, problems_text(data), reply_markup=problems_kb(await site_link(message.bot, uid, "errors")))


async def _fresh(message: Message, state: FSMContext) -> None:
    """Команда: убрать её и прошлый экран, новый экран — один внизу."""
    await safe_delete(message)
    await drop_screen(message, state)


@router.message(Command("ferma", "farm"))
async def cmd_farm(message: Message, state: FSMContext) -> None:
    await _fresh(message, state)
    await show_farm(message, state, message.from_user.id)


@router.message(Command("problems", "problemy"))
async def cmd_problems(message: Message, state: FSMContext) -> None:
    await _fresh(message, state)
    await show_problems(message, state, message.from_user.id)


@router.message(Command("site", "sait"))
async def cmd_site(message: Message, state: FSMContext) -> None:
    await _fresh(message, state)
    url = await site_link(message.bot, message.from_user.id)
    await edit_live_screen(message, state, "🌐 Сайт Kirka — вход уже выполнен, ссылка действует 30 минут.",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("🌐 Открыть Kirka", url=url, style="success")], [btn("☰ Меню", "hub:menu")]]))


@router.callback_query(F.data == "hub:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await set_screen_id(state, cb.message.message_id)
    await show_hub(cb.message, state, cb.from_user.id)
    await cb.answer()


@router.callback_query(F.data.in_({"hub:farm", "hub:farm:r"}))
async def cb_farm(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer("Обновлено" if cb.data.endswith(":r") else None)
    await set_screen_id(state, cb.message.message_id)
    await show_farm(cb.message, state, cb.from_user.id)


@router.callback_query(F.data.in_({"hub:problems", "hub:problems:r"}))
async def cb_problems(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer("Обновлено" if cb.data.endswith(":r") else None)
    await set_screen_id(state, cb.message.message_id)
    await show_problems(cb.message, state, cb.from_user.id)


@router.message(Command("help", "pomosh"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await _fresh(message, state)
    await edit_live_screen(message, state, HELP_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("☰ Меню", "hub:menu")]]))


@router.callback_query(F.data == "hub:help")
async def cb_help(cb: CallbackQuery, state: FSMContext) -> None:
    await set_screen_id(state, cb.message.message_id)
    await edit_live_screen(cb.message, state, HELP_TEXT, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("☰ Меню", "hub:menu")]]))
    await cb.answer()
