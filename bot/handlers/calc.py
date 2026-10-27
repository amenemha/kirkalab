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
from bot.handlers.export import handle_export
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
from bot.live_screen import edit_live_screen, safe_delete, set_screen_id

router = Router()


class CalcStates(StatesGroup):
  manual_hashrate = State()
  manual_power = State()
  price = State()
  save_price = State()
  quantity = State()


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
  await set_screen_id(state, callback.message.message_id)
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
  await set_screen_id(state, callback.message.message_id)
  await _ask_quantity(callback.message, state, title)
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
  await safe_delete(message)
  if value is None or value <= 0:
    await edit_live_screen(
      message,
      state,
      "✍️ <b>Ручной ввод</b>\n\n"
      "Введите положительное число, например <code>110</code>:",
    )
    return
  await state.update_data(hashrate_ths=str(value))
  await state.set_state(CalcStates.manual_power)
  await edit_live_screen(
    message,
    state,
    "✍️ <b>Ручной ввод</b>\n\n"
    "Теперь введите потребление в Вт (например, <code>3250</code>):",
  )


@router.message(CalcStates.manual_power, F.text)
async def manual_power(message: Message, state: FSMContext) -> None:
  value = _parse_decimal(message.text)
  await safe_delete(message)
  if value is None or value < 1:
    await edit_live_screen(
      message,
      state,
      "✍️ <b>Ручной ввод</b>\n\n"
      "Введите потребление в Вт, например <code>3250</code>:",
    )
    return
  await state.update_data(power_w=int(value))
  await state.set_state(None)
  data = await state.get_data()
  await _ask_quantity(message, state, data.get("title", "Своё оборудование"))


# --------------------------------------------------------------------------- #
# Quantity.
# --------------------------------------------------------------------------- #
async def _ask_quantity(message: Message, state: FSMContext, title: str) -> None:
  await edit_live_screen(
    message,
    state,
    f"🧮 <b>{title}</b>\n\nСколько устройств? Выберите или введите число.",
    reply_markup=calc_quantity_kb(),
  )


@router.callback_query(F.data.startswith("calc:qty:"))
async def cb_quantity(callback: CallbackQuery, state: FSMContext) -> None:
  if callback.data == "calc:qty:other":
    await state.set_state(CalcStates.quantity)
    await callback.message.edit_text("🧮 Введите количество устройств (1–10000):")
    await callback.answer()
    return
  qty = _parse_id(callback.data)
  if qty is None or not (1 <= qty <= 10000):
    await callback.answer("Введите число от 1 до 10000.", show_alert=True)
    return
  await state.update_data(quantity=qty)
  await _ask_price(callback, state)
  await callback.answer()


@router.message(CalcStates.quantity, F.text)
async def quantity_entered(message: Message, state: FSMContext) -> None:
  raw = (message.text or "").strip().replace(" ", "")
  await safe_delete(message)
  if not raw.isdigit() or not (1 <= int(raw) <= 10000):
    await edit_live_screen(message, state, "🧮 Введите целое число от 1 до 10000:")
    return
  await state.update_data(quantity=int(raw))
  await state.set_state(None)
  await _ask_price_msg(message, state)


# --------------------------------------------------------------------------- #
# Power price.
# --------------------------------------------------------------------------- #
PRICE_TEXT = (
  "💡 <b>Стоимость электроэнергии</b>\n\n"
  "Сколько стоит кВт·ч? Можно в рублях (<code>5.5</code>) "
  "или в USDT (<code>0.06 usdt</code>)."
)


async def _saved_price(event, state: FSMContext, user_id: int) -> tuple[str | None, str]:
  secret = _secret()
  if not secret:
    return None, "RUB"
  try:
    status = await _client(event).calc_status(telegram_user_id=user_id, bot_secret=secret)
  except ApiError:
    return None, "RUB"
  saved = status.get("default_power_price")
  cur = (status.get("currency") or "USDT").upper()
  if saved is None:
    return None, cur
  price = _trim(str(saved))
  await state.update_data(saved_price=price, saved_price_currency=cur)
  return price, cur


async def _ask_price(callback: CallbackQuery, state: FSMContext) -> None:
  saved_price, cur = await _saved_price(callback, state, callback.from_user.id)
  await callback.message.edit_text(PRICE_TEXT, reply_markup=calc_price_kb(saved_price, cur))


async def _ask_price_msg(message: Message, state: FSMContext) -> None:
  saved_price, cur = await _saved_price(message, state, message.chat.id)
  await edit_live_screen(message, state, PRICE_TEXT, reply_markup=calc_price_kb(saved_price, cur))


@router.callback_query(F.data == "calc:price:saved")
async def cb_price_saved(callback: CallbackQuery, state: FSMContext) -> None:
  data = await state.get_data()
  price = data.get("saved_price")
  if price is None:
    await callback.answer("Сохранённая стоимость не найдена.", show_alert=True)
    return
  await state.update_data(power_price=str(price), save_power_price=False,
                          price_currency=data.get("saved_price_currency") or "USDT")
  await _run_and_render(callback.message, callback.from_user.id, state)
  await callback.answer()


@router.callback_query(F.data == "calc:price:new")
async def cb_price_new(callback: CallbackQuery, state: FSMContext) -> None:
  await state.set_state(CalcStates.price)
  await callback.message.edit_text(
    "💡 Введите стоимость кВт·ч: в рублях — <code>5.5</code>, "
    "в USDT — <code>0.06 usdt</code>:"
  )
  await callback.answer()


@router.message(CalcStates.price, F.text)
async def price_entered(message: Message, state: FSMContext) -> None:
  raw = (message.text or "").strip().lower()
  cur = "RUB"
  for tag in ("usdt", "usd", "$"):
    if tag in raw:
      cur = "USDT"
      raw = raw.replace(tag, "")
  for tag in ("руб", "р", "₽", "rub"):
    raw = raw.replace(tag, "")
  value = _parse_decimal(raw.strip().rstrip("."))
  await safe_delete(message)
  if value is None or value < 0:
    await edit_live_screen(
      message,
      state,
      "💡 Введите число, например <code>5.5</code> (₽) или <code>0.06 usdt</code>:",
    )
    return
  await state.update_data(power_price=str(value), price_currency=cur)
  await state.set_state(CalcStates.save_price)
  kb = InlineKeyboardMarkup(
    inline_keyboard=[
      [
        InlineKeyboardButton(text="💾 Сохранить", callback_data="calc:save:1"),
        InlineKeyboardButton(text="Не сохранять", callback_data="calc:save:0"),
      ]
    ]
  )
  await edit_live_screen(
    message,
    state,
    "Сохранить эту стоимость для будущих расчётов?",
    reply_markup=kb,
  )


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
    "currency": "RUB",
    "power_price_currency": data.get("price_currency") or "RUB",
    "save_power_price": bool(data.get("save_power_price", False)),
    "device_name": data.get("title"),
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
    currency="RUB",
    local_view=resp.get("local"),
  )
  run_id = resp.get("run_id")
  await state.update_data(has_firmware=bool(resp.get("has_firmware")))
  await message.edit_text(
    text,
    reply_markup=calc_result_kb(
      has_firmware=bool(resp.get("has_firmware")),
      run_id=int(run_id) if run_id is not None else None,
    ),
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
    "🔧 Сравнение с прошивкой — скоро.",
    show_alert=True,
  )


@router.callback_query(F.data.startswith("calc:xlsx:"))
async def cb_export(callback: CallbackQuery, state: FSMContext) -> None:
  # Excel export of the just-computed run. PRO gate + ownership are enforced
  # server-side; a FREE user sees a soft upsell whose "back" returns to a fresh
  # calculation (the result screen state may be gone after the document send).
  await handle_export(callback, state, back_callback="calc:restart")


@router.callback_query(F.data == "calc:pro")
async def cb_pro_stub(callback: CallbackQuery) -> None:
  await callback.message.edit_text(
    "🧪 <b>Тестовый период</b>\n\nСейчас всё открыто для всех.",
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
