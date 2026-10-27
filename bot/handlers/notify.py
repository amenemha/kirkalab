"""🔔 Уведомления фермы: что присылать, порог ошибок, задержка, сон, сводка."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings

router = Router(name="notify")

HOLD = [0, 5, 10, 15, 30, 60]
EVERY = [5, 15, 30, 60, 180]
WAKE = [0, 10, 30, 100]
SLEEPS = [("23:00", "08:00"), ("22:00", "07:00"), ("00:00", "09:00"), ("01:00", "08:00")]
DAILY = ["07:00", "08:00", "09:00", "10:00"]
LVL_SHORT = {0: "все", 1: "предупреждения+", 2: "серьёзные+", 3: "только критичные"}


def _hdr() -> dict:
  return {"X-Bot-Secret": get_settings().bot_internal_secret or ""}


async def _get(api, uid: int) -> tuple[int, dict]:
  r = await api._request("GET", "/api/v1/internal/notify", params={"telegram_user_id": uid}, headers=_hdr())
  return r.status_code, (r.json() if r.status_code == 200 else {})


async def _put(api, uid: int, values: dict) -> tuple[int, dict]:
  r = await api._request("PUT", "/api/v1/internal/notify", json={"telegram_user_id": uid, "values": values}, headers=_hdr())
  return r.status_code, (r.json() if r.status_code == 200 else {})


def _next(seq: list, cur):
  try:
    return seq[(seq.index(cur) + 1) % len(seq)]
  except ValueError:
    return seq[0]


def render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
  p = data["prefs"]
  types = data["types"]
  s = p["sleep"]
  on = p["enabled"]
  lines = [
    "🔔 <b>Уведомления фермы</b>",
    "",
    f"Статус: {'✅ включены' if on else '⏹ выключены'}",
    f"Ошибки: {LVL_SHORT.get(p['min_level'], p['min_level'])}",
    f"Сообщать, если держится ≥ {p['hold_min']} мин (без «мигания»)",
    f"Сводка изменений: раз в {p['every_min']} мин",
    f"Сон: {s['from']}–{s['to']}" if s["on"] else "Сон: выключен",
    f"Будить во сне: {'при отключении ≥ ' + str(p['wake_offline']) + ' асиков' if p['wake_offline'] else 'нет'}",
    f"Утренняя сводка: {p['daily_at']}" if p["types"].get("daily") else "Утренняя сводка: выключена",
    "",
    "Нажмите на пункт, чтобы переключить.",
  ]
  rows = [[InlineKeyboardButton(text=("⏹ Выключить все" if on else "▶️ Включить"), callback_data="notif:on")]]
  pair: list = []
  for k, label in types.items():
    mark = "✅" if p["types"].get(k) else "▫️"
    pair.append(InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"notif:t:{k}"))
    if len(pair) == 2:
      rows.append(pair)
      pair = []
  if pair:
    rows.append(pair)
  rows += [
    [InlineKeyboardButton(text=f"Ошибки: {LVL_SHORT.get(p['min_level'])}", callback_data="notif:lvl"),
     InlineKeyboardButton(text=f"Держится ≥ {p['hold_min']} мин", callback_data="notif:hold")],
    [InlineKeyboardButton(text=f"Раз в {p['every_min']} мин", callback_data="notif:every"),
     InlineKeyboardButton(text=f"Сводка {p['daily_at']}", callback_data="notif:daily")],
    [InlineKeyboardButton(text=("😴 Сон " + f"{s['from']}–{s['to']}" if s["on"] else "😴 Сон: выкл"), callback_data="notif:sleep"),
     InlineKeyboardButton(text="🕒 Время сна", callback_data="notif:sleept")],
    [InlineKeyboardButton(text=f"Будить: {'≥ ' + str(p['wake_offline']) if p['wake_offline'] else 'нет'}", callback_data="notif:wake")],
    [InlineKeyboardButton(text="📨 Прислать проверку", callback_data="notif:test")],
    [InlineKeyboardButton(text="☰ Меню", callback_data="hub:menu")],
  ]
  return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


DENIED = "🔔 Уведомления фермы доступны управляющим площадки."


def _is_digest(msg: Message | None) -> bool:
  t = (msg.text or "") if msg else ""
  return t.startswith("🔔 Kirka") or t.startswith("☀️ Kirka")


@router.message(Command("notify"))
async def cmd_notify(message: Message) -> None:
  code, data = await _get(message.bot.kirkalab_client, message.from_user.id)
  if code != 200:
    await message.answer(DENIED)
    return
  text, kb = render(data)
  await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("notif:"))
async def cb_notify(callback: CallbackQuery) -> None:
  api = callback.bot.kirkalab_client
  uid = callback.from_user.id
  act = callback.data.split(":", 2)[1:]
  code, data = await _get(api, uid)
  if code != 200:
    await callback.answer(DENIED, show_alert=True)
    return
  p = data["prefs"]
  values: dict | None = None
  a = act[0]
  if a == "on":
    values = {"enabled": not p["enabled"]}
  elif a == "t" and len(act) > 1:
    values = {"types": {act[1]: not p["types"].get(act[1])}}
  elif a == "lvl":
    values = {"min_level": (p["min_level"] + 1) % 4}
  elif a == "hold":
    values = {"hold_min": _next(HOLD, p["hold_min"])}
  elif a == "every":
    values = {"every_min": _next(EVERY, p["every_min"])}
  elif a == "daily":
    values = {"daily_at": _next(DAILY, p["daily_at"]), "types": {"daily": True}}
  elif a == "sleep":
    values = {"sleep": {"on": not p["sleep"]["on"]}}
  elif a == "sleept":
    f, t = _next(SLEEPS, (p["sleep"]["from"], p["sleep"]["to"]))
    values = {"sleep": {"on": True, "from": f, "to": t}}
  elif a == "wake":
    values = {"wake_offline": _next(WAKE, p["wake_offline"])}
  elif a == "test":
    r = await api._request("POST", "/api/v1/internal/notify/test", json={"telegram_user_id": uid}, headers=_hdr())
    ok = r.status_code == 200 and r.json().get("sent")
    await callback.answer("📨 Проверка отправлена" if ok else "Не удалось отправить", show_alert=not ok)
    return
  if values is not None:
    code, data = await _put(api, uid, values)
    if code != 200:
      await callback.answer("Не удалось сохранить", show_alert=True)
      return
  text, kb = render(data)
  if a == "open" and _is_digest(callback.message):
    await callback.message.answer(text, reply_markup=kb)
  else:
    try:
      await callback.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001 — "message is not modified" и т.п.
      pass
  await callback.answer("Сохранено" if values is not None else None)
