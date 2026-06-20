"""Profitability calculation flow (FSM, Rapira style).

Full user scenario: equipment → quantity → power price → result screen, with
the FREE funnel/limits applied server-side. The bot never duplicates the
business rules — it asks the internal API (``/internal/calc``) which returns the
result *plus* funnel metadata, and renders accordingly.

Flow / callbacks (``calc:*`` namespace; the read-only catalog uses ``cat:*``):

  calc:start            equipment-source picker (catalog / manual)
  calc:cat              brand picker (calc mode)
  calc:b:<brand>:<page> model page (calc mode)
  calc:m:<model_id>     model chosen → quantity step
  calc:manual           manual entry → ask hashrate, then power
  calc:qty:<n>          quantity chosen → power-price step
  calc:price:saved      reuse the user's saved kWh price → calculate
  calc:price:new        ask for a new price (text), optionally save
  calc:save:<0|1>       answer the "save price?" prompt → calculate
  calc:restart          recalculate (back to the start)
  calc:compare          firmware comparison (stock + soft PRO invite)
  calc:pro              PRO "coming soon" stub

Specs/state live in the aiogram FSM context; the per-user PRO status and funnel
position are resolved server-side from ``telegram_user_id``.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.api_client import ApiError, KirkalabApiClient
from bot.calc_format import (
    format_limit_reached,
    format_result_screen,
)
from bot.config import get_settings
from bot.keyboards import (
    PAGE_SIZE,
    calc_brand_list_kb,
    calc_model_page_kb,
    calc_price_kb,
    calc_pro_stub_kb,
    calc_quantity_kb,
    calc_result_kb,
    calc_start_kb,
)

router = Router()


class CalcStates(StatesGroup):
  manual_hashrate = State()
  manual_power = State()
  price = State()
  save_price = State()


def _client(event: Message | CallbackQuery) -> KirkalabApiClient:
  return event.bot.kirkalab_client


def _secret() -> str | None:
  return get_settings().bot_internal_secret


START_TEXT = (
  "🧮 <b>Расчёт доходности</b>\n\n"
  "Выберите оборудование из каталога или введите параметры вручную."
)


# --------------------------------------------------------------------------- #
# Entry: from the main menu "🧮 Рассчитать доходность" (menu:calculator).
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "menu:calculator")
@router.callback_query(F.data == "calc:start")
@router.callback_query(F.data == "calc:restart")
async def cb_start(callback: CallbackQuery, state: FSMContext) -> None:
  await state.clear()
  await callback.message.edit_text(START_TEXT, reply_markup=calc_start_kb())
  await callback.answer()


# --------------------------------------------------------------------------- #
# Equipment selection — catalog navigation (calc mode).
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "calc:cat")
async def cb_brands(callback: CallbackQuery) -> None:
  try:
    brands = await _client(callback).list_brands()
  except ApiError as exc:
    await callback.answer(f"⚠️ {exc.message}", show_alert=True)
    return
  await callback.message.edit_text(
    "🧮 <b>Расчёт доходности</b>\n\nВыберите бренд:",
    reply_markup=calc_brand_list_kb(brands),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("calc:b:"))
async def cb_models(callback: CallbackQuery) -> None:
  _, _, brand, page_str = callback.data.split(":", 3)
  try:
    page = int(page_str)
  except ValueError:
    page = 0
  skip = page * PAGE_SIZE
  try:
    data = await _client(callback).list_models_by_brand(
      brand, skip=skip, limit=PAGE_SIZE
    )
  except ApiError as exc:
    await callback.answer(f"⚠️ {exc.message}", show_alert=True)
    return

  items = data.get("items", [])
  total = data.get("total", 0)
  if not items:
    await callback.answer("Модели не найдены.", show_alert=True)
    return

  last_page = max((total - 1) // PAGE_SIZE, 0)
  page = min(page, last_page)
  await callback.message.edit_text(
    f"🧮 <b>{brand}</b>\n\nВыберите модель для расчёта:",
    reply_markup=calc_model_page_kb(
      brand=brand, items=items, page=page, last_page=last_page
    ),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("calc:m:"))
async def cb_model_chosen(callback: CallbackQuery, state: FSMContext) -> None:
  model_id = _parse_id(callback.data)
  if model_id is None:
    await callback.answer()
    return
  try:
    model = await _client(callback).get_model(model_id)
  except ApiError as exc:
    await callback.answer(f"⚠️ {exc.message}", show_alert=True)
    return

  title = _model_title(model)
  await state.update_data(
    device_model_id=model_id,
    title=title,
    hashrate_ths=None,
    power_w=None,
  )
  await _ask_quantity(callback.message, title)
  await callback.answer()


# --------------------------------------------------------------------------- #
# Manual entry path.
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "calc:manual")
async def cb_manual(callback: CallbackQuery, state: FSMContext) -> None:
  await state.update_data(device_model_id=None, title="Своё оборудование")
  await state.set_state(CalcStates.manual_hashrate)
  await callback.message.edit_text(
    "✍️ <b>Ручной ввод</b>\n\n"
    "Введите хешрейт в TH/s (например, <code>110</code>):"
  )
  await callback.answer()


@router.message(CalcStates.manual_hashrate, F.text)
async def manual_hashrate(message: Message, state: FSMContext) -> None:
  value = _parse_decimal(message.text)
  if value is None or value <= 0:
    await message.answer("Введите положительное число, например 110.")
    return
  await state.update_data(hashrate_ths=str(value))
  await state.set_state(CalcStates.manual_power)
  await message.answer("Теперь введите потребление в Вт (например, 3250):")


@router.message(CalcStates.manual_power, F.text)
async def manual_power(message: Message, state: FSMContext) -> None:
  value = _parse_decimal(message.text)
  if value is None or value < 1:
    await message.answer("Введите потребление в Вт, например 3250.")
    return
  await state.update_data(power_w=int(value))
  await state.set_state(None)
  data = await state.get_data()
  await _ask_quantity(message, data.get("title", "Своё оборудование"))


# --------------------------------------------------------------------------- #
# Quantity.
# --------------------------------------------------------------------------- #
async def _ask_quantity(message: Message, title: str) -> None:
  await message.answer(
    f"🧮 <b>{title}</b>\n\nСколько устройств? (1–5)",
    reply_markup=calc_quantity_kb(),
  )


@router.callback_query(F.data.startswith("calc:qty:"))
async def cb_quantity(callback: CallbackQuery, state: FSMContext) -> None:
  qty = _parse_id(callback.data)
  if qty is None or not (1 <= qty <= 5):
    await callback.answer(
      "На бесплатном тарифе — до 5 устройств одного типа. "
      "Больше и разное оборудование — в PRO 💎",
      show_alert=True,
    )
    return
  await state.update_data(quantity=qty)
  await _ask_price(callback, state)
  await callback.answer()


# --------------------------------------------------------------------------- #
# Power price.
# --------------------------------------------------------------------------- #
async def _ask_price(callback: CallbackQuery, state: FSMContext) -> None:
  saved_price = None
  secret = _secret()
  if secret:
    try:
      status = await _client(callback).calc_status(
        telegram_user_id=callback.from_user.id, bot_secret=secret
      )
      saved = status.get("default_power_price")
      if saved is not None:
        saved_price = _trim(str(saved))
        await state.update_data(saved_price=saved_price)
    except ApiError:
      saved_price = None
  await callback.message.edit_text(
    "💡 <b>Цена электроэнергии</b>\n\n"
    "Укажите тариф в USDT за кВт·ч.\n"
    "<i>Другие валюты (₽/$/¥) — в PRO.</i>",
    reply_markup=calc_price_kb(saved_price),
  )


@router.callback_query(F.data == "calc:price:saved")
async def cb_price_saved(callback: CallbackQuery, state: FSMContext) -> None:
  data = await state.get_data()
  price = data.get("saved_price")
  if price is None:
    await callback.answer("Сохранённая цена не найдена.", show_alert=True)
    return
  await state.update_data(power_price=str(price), save_power_price=False)
  await _run_and_render(callback.message, callback.from_user.id, state)
  await callback.answer()


@router.callback_query(F.data == "calc:price:new")
async def cb_price_new(callback: CallbackQuery, state: FSMContext) -> None:
  await state.set_state(CalcStates.price)
  await callback.message.edit_text(
    "💡 Введите цену электроэнергии в USDT за кВт·ч "
    "(например, <code>0.05</code>):"
  )
  await callback.answer()


@router.message(CalcStates.price, F.text)
async def price_entered(message: Message, state: FSMContext) -> None:
  value = _parse_decimal(message.text)
  if value is None or value < 0:
    await message.answer("Введите неотрицательное число, например 0.05.")
    return
  await state.update_data(power_price=str(value))
  await state.set_state(CalcStates.save_price)
  kb = InlineKeyboardMarkup(
    inline_keyboard=[
      [
        InlineKeyboardButton(text="💾 Сохранить", callback_data="calc:save:1"),
        InlineKeyboardButton(text="Не сохранять", callback_data="calc:save:0"),
      ]
    ]
  )
  await message.answer("Сохранить эту цену для будущих расчётов?", reply_markup=kb)


@router.callback_query(CalcStates.save_price, F.data.startswith("calc:save:"))
async def cb_save_price(callback: CallbackQuery, state: FSMContext) -> None:
  save = callback.data.rsplit(":", 1)[1] == "1"
  await state.update_data(save_power_price=save)
  await state.set_state(None)
  await _run_and_render(callback.message, callback.from_user.id, state)
  await callback.answer()


# --------------------------------------------------------------------------- #
# Calculate + render the result screen.
# --------------------------------------------------------------------------- #
async def _run_and_render(
  message: Message, telegram_user_id: int, state: FSMContext
) -> None:
  secret = _secret()
  if not secret:
    await message.edit_text(
      "⚠️ Бот не настроен для расчётов. Обратитесь в поддержку."
    )
    return

  data = await state.get_data()
  payload: dict = {
    "telegram_user_id": telegram_user_id,
    "quantity": int(data.get("quantity", 1)),
    "power_price": str(data.get("power_price", "0")),
    "currency": "USDT",
    "save_power_price": bool(data.get("save_power_price", False)),
  }
  if data.get("device_model_id") is not None:
    payload["device_model_id"] = int(data["device_model_id"])
  else:
    payload["hashrate_ths"] = str(data.get("hashrate_ths"))
    payload["power_w"] = int(data.get("power_w"))

  try:
    resp = await _client(message).internal_calc(payload, bot_secret=secret)
  except ApiError as exc:
    await message.edit_text(f"❌ Не удалось рассчитать: {exc.message}")
    return

  funnel = resp.get("funnel", {})
  if not resp.get("allowed", False):
    await message.edit_text(
      format_limit_reached(funnel), reply_markup=calc_pro_stub_kb()
    )
    return

  result = resp.get("result") or {}
  text = format_result_screen(
    result=result,
    funnel=funnel,
    title=data.get("title", "Оборудование"),
    quantity=int(data.get("quantity", 1)),
    currency="USDT",
  )
  await state.update_data(has_firmware=bool(resp.get("has_firmware")))
  await message.edit_text(
    text,
    reply_markup=calc_result_kb(has_firmware=bool(resp.get("has_firmware"))),
    disable_web_page_preview=True,
  )


# --------------------------------------------------------------------------- #
# Firmware comparison + PRO stub.
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "calc:compare")
async def cb_compare(callback: CallbackQuery, state: FSMContext) -> None:
  # Compare against stock is informative for everyone; the economy delta is a
  # PRO unlock, so here we surface a soft invite (the /calc/compare endpoint
  # withholds the delta for non-PRO callers anyway). A full compare UI is a
  # follow-up; this keeps the result screen's promise without a dead end.
  await callback.answer(
    "🔧 Сравнение с кастомной прошивкой и экономия — в PRO 💎",
    show_alert=True,
  )


@router.callback_query(F.data == "calc:pro")
async def cb_pro_stub(callback: CallbackQuery) -> None:
  await callback.message.edit_text(
    "💎 <b>PRO — скоро</b>\n\n"
    "Безлимитные расчёты, все валюты (₽/$/¥), окупаемость и ROI без блюра, "
    "сравнение прошивок и сохранение сборок.\n\n"
    "Подписка скоро будет доступна — спасибо, что вы с нами! 🙌",
    reply_markup=calc_pro_stub_kb(),
  )
  await callback.answer()


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _parse_id(data: str) -> int | None:
  try:
    return int(data.rsplit(":", 1)[1])
  except (IndexError, ValueError):
    return None


def _parse_decimal(text: str | None) -> Decimal | None:
  if not text:
    return None
  cleaned = text.strip().replace(",", ".").replace(" ", "")
  try:
    return Decimal(cleaned)
  except (InvalidOperation, ValueError):
    return None


def _trim(value: str) -> str:
  if "." in value:
    value = value.rstrip("0").rstrip(".")
  return value or "0"


def _model_title(model: dict) -> str:
  name = model.get("model_name", "")
  variant = model.get("variant")
  brand = model.get("brand", "")
  if variant:
    return f"{brand} {name} {variant}".strip()
  return f"{brand} {name}".strip()
