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
    "🌐 <b>Открыть Kirka</b> — сайт с картой контейнеров, стеллажами и полками.\n\n"
    "Команды: /menu, /ferma, /problems, /pools, /site, /help"
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


def farm_text(s: dict) -> str:
    from datetime import datetime, timedelta, timezone

    when = ""
    if s.get("synced_at"):
        when = datetime.fromisoformat(s["synced_at"]).astimezone(timezone(timedelta(hours=3))).strftime("%H:%M")
    total, work, off = s.get("total") or 0, s.get("working") or 0, s.get("offline") or 0
    susp, al = s.get("suspended") or 0, s.get("with_alarms") or 0
    head = "🟢" if not off else ("🔴" if off > total * 0.05 else "🟡")
    pct = work / total * 100 if total else 0
    lines = [f"🏭 <b>Ферма</b> · <i>{when} МСК</i>", "",
             f"{head} В работе <b>{_n(work)}</b> из {_n(total)} ({_n(pct)}%)",
             f"⛏ Хешрейт <b>{_n((s.get('ths') or 0) / 1000, 2)} PH/s</b>"
             + (f" · {_n(s.get('efficiency'))}% от паспорта" if s.get("efficiency") else ""),
             f"⚡ Мощность <b>{_n((s.get('kw') or 0) / 1000, 2)} МВт</b>", ""]
    lines.append(f"🔴 Не работают: <b>{_n(off)}</b> {plural(off, 'асик', 'асика', 'асиков')}" if off else "✅ Все асики на связи")
    if susp:
        lines.append(f"⏸ На паузе: {_n(susp)}")
    if al:
        lines.append(f"🟡 С ошибками: {_n(al)} {plural(al, 'асик', 'асика', 'асиков')}")
    inc = [i for i in s.get("incidents") or [] if not i.get("ended")]
    if inc:
        lines += ["", "🚨 <b>Аварии сейчас</b>"] + [f"• {short_name(i['scope'])}: {i['down']} из {i['total']}" for i in inc[:5]]
    sites = [x for x in s.get("sites") or [] if x.get("total")]
    if len(sites) > 1:
        lines += ["", "<b>Площадки</b>"] + [f"• {short_name(x['name'])}: {_n(x.get('working'))} из {_n(x['total'])}" for x in sites]
    lines += ["", "Подробности по ошибкам — в «Проблемах»."]
    return "\n".join(lines)


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
        [btn("🌊 Пулы", "pools:refresh"), btn("🧮 Расчёт", "menu:calculator")],
        [btn("📋 Каталог ASIC", "menu:catalog"), btn("📊 Мои отчёты", "menu:reports")],
        [btn("👤 Профиль", "menu:profile"), btn("ℹ️ Помощь", "hub:help")],
        [btn("🌐 Открыть Kirka", url=url, style="success")],
    ])


def farm_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("🔁 Обновить", "hub:farm:r"), btn("⚠️ Проблемы", "hub:problems")],
        [btn("📊 Открыть на сайте", url=url, style="primary")],
        [btn("☰ Меню", "hub:menu")],
    ])


def problems_text(s: dict) -> str:
    n = lambda x: f"{x:,}".replace(",", " ")  # noqa: E731
    lines = ["⚠️ <b>Проблемы сейчас</b>", "",
             f"🔴 Не работают: <b>{n(s.get('offline') or 0)}</b> из {n(s.get('total') or 0)}",
             f"🟡 С ошибками: <b>{n(s.get('with_alarms') or 0)}</b> {plural(s.get('with_alarms') or 0, 'асик', 'асика', 'асиков')}"]
    inc = [i for i in s.get("incidents") or [] if not i.get("ended")]
    if inc:
        lines += ["", "<b>🚨 Аварии</b>"] + [f"• {i['scope']}: {i['down']} из {i['total']} не работают" for i in inc[:5]]
    cls = sorted((c for c in s.get("classes") or [] if c.get("count")), key=lambda c: -c["count"])
    if cls:
        lines += ["", "<b>Ошибки по типу</b>"] + [f"• {c['label']} — {n(c['count'])}" for c in cls]
    worst = sorted((c for c in s.get("containers") or [] if c.get("total") and c.get("offline")),
                   key=lambda c: c["offline"] / c["total"], reverse=True)[:6]
    if worst:
        lines += ["", "<b>Где больше всего не работает</b>"] + [
            f"• {short_name(c['name'])} — {c['offline']} из {c['total']}" for c in worst]
    if len(lines) <= 4 and not (s.get("offline") or s.get("with_alarms")):
        lines += ["", "✅ Всё спокойно."]
    return "\n".join(lines)


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
        await edit_live_screen(message, state, "🏭 Ферма пока доступна только администраторам.",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("☰ Меню", "hub:menu")]]))
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
