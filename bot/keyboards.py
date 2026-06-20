"""Inline keyboards for the Kirkalab bot (Rapira-style navigation).

Callback data uses a stable ``menu:<action>`` namespace so new menu items
can be added without touching unrelated handlers.
"""
from __future__ import annotations

from aiogram.types import (
  InlineKeyboardButton,
  InlineKeyboardMarkup,
  KeyboardButton,
  ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.menu_items import MAIN_MENU_ITEMS, REPLY_MENU_ITEMS, STUB_ACTIONS

__all__ = [
  "MAIN_MENU_ITEMS",
  "REPLY_MENU_ITEMS",
  "STUB_ACTIONS",
  "PAGE_SIZE",
  "main_menu",
  "main_reply_menu",
  "profile_menu",
  "back_to_menu",
  "qr_confirm",
  "brand_list_kb",
  "model_page_kb",
  "device_card_kb",
  "firmware_back_kb",
  "calc_start_kb",
  "calc_brand_list_kb",
  "calc_model_page_kb",
  "calc_quantity_kb",
  "calc_price_kb",
  "calc_result_kb",
  "calc_pro_stub_kb",
  "plans_kb",
]

# Models shown per page on the brand -> model screen. Kept small so the inline
# keyboard stays well under Telegram's button limits.
PAGE_SIZE = 8


def main_menu() -> InlineKeyboardMarkup:
  """Build the main inline menu, two buttons per row."""
  builder = InlineKeyboardBuilder()
  for action, label in MAIN_MENU_ITEMS:
    builder.button(text=label, callback_data=f"menu:{action}")
  builder.adjust(2)
  return builder.as_markup()


def main_reply_menu() -> ReplyKeyboardMarkup:
  """Persistent reply keyboard, 4 buttons laid out 2×2 (CALC_SPEC §3.2).

  Tariff/PRO and Help are intentionally absent — they live inside Profile."""
  builder = ReplyKeyboardBuilder()
  for _action, text in REPLY_MENU_ITEMS:
    builder.add(KeyboardButton(text=text))
  builder.adjust(2, 2)
  return builder.as_markup(resize_keyboard=True, is_persistent=True)


def profile_menu(*, is_pro: bool) -> InlineKeyboardMarkup:
  """Inline menu inside Profile: the home for the rare/personal stuff.

  Tariff/PRO and Help live here (not in the main menu). A non-PRO user is
  offered the optional email/password link to an existing PRO/web account."""
  builder = InlineKeyboardBuilder()
  if not is_pro:
    builder.button(
      text="🔗 Связать PRO-аккаунт", callback_data="profile:link"
    )
  builder.button(text="💎 Тариф", callback_data="profile:plan")
  builder.button(text="ℹ️ Помощь", callback_data="profile:help")
  builder.adjust(1)
  return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
  """A single button that returns to the main menu."""
  return InlineKeyboardMarkup(
    inline_keyboard=[
      [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")]
    ]
  )


def brand_list_kb(brands: list[dict]) -> InlineKeyboardMarkup:
  """Brand picker: one button per brand (with model count), two per row."""
  builder = InlineKeyboardBuilder()
  for entry in brands:
    brand = entry["brand"]
    count = entry.get("model_count")
    label = f"{brand} ({count})" if count is not None else brand
    builder.button(text=label, callback_data=f"cat:b:{brand}:0")
  builder.adjust(2)
  builder.row(
    InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")
  )
  return builder.as_markup()


def model_page_kb(
  *, brand: str, items: list[dict], page: int, last_page: int
) -> InlineKeyboardMarkup:
  """One page of models plus pagination + navigation rows."""
  builder = InlineKeyboardBuilder()
  for item in items:
    variant = item.get("variant")
    name = item["model_name"]
    label = f"{name} {variant}" if variant else name
    builder.button(text=label, callback_data=f"cat:m:{item['id']}")
  builder.adjust(1)

  nav: list[InlineKeyboardButton] = []
  if page > 0:
    nav.append(
      InlineKeyboardButton(
        text="‹ Назад", callback_data=f"cat:b:{brand}:{page - 1}"
      )
    )
  nav.append(
    InlineKeyboardButton(
      text=f"{page + 1}/{last_page + 1}", callback_data="cat:noop"
    )
  )
  if page < last_page:
    nav.append(
      InlineKeyboardButton(
        text="Далее ›", callback_data=f"cat:b:{brand}:{page + 1}"
      )
    )
  builder.row(*nav)
  builder.row(
    InlineKeyboardButton(text="‹ К брендам", callback_data="cat:home")
  )
  return builder.as_markup()


def device_card_kb(
  *, brand: str, model_id: int, has_firmware: bool
) -> InlineKeyboardMarkup:
  """Navigation under a device card; firmware button only when presets exist."""
  builder = InlineKeyboardBuilder()
  if has_firmware:
    builder.button(
      text="🔧 Кастомные прошивки", callback_data=f"cat:fw:{model_id}"
    )
    builder.adjust(1)
  builder.row(
    InlineKeyboardButton(
      text="‹ К моделям", callback_data=f"cat:b:{brand}:0"
    ),
    InlineKeyboardButton(text="‹ К брендам", callback_data="cat:home"),
  )
  return builder.as_markup()


def firmware_back_kb(model_id: int) -> InlineKeyboardMarkup:
  """Return-to-card button shown under the firmware-preset list."""
  return InlineKeyboardMarkup(
    inline_keyboard=[
      [
        InlineKeyboardButton(
          text="‹ К карточке", callback_data=f"cat:m:{model_id}"
        )
      ]
    ]
  )


# ---------------------------------------------------------------------------
# Profitability calculation flow (Rapira style). A dedicated ``calc:*``
# namespace so the catalog navigation can be reused without colliding with the
# read-only ``cat:*`` browse flow (model pick leads into the flow, not a card).
# ---------------------------------------------------------------------------


def calc_start_kb() -> InlineKeyboardMarkup:
  """Equipment source picker: catalog vs manual entry."""
  builder = InlineKeyboardBuilder()
  builder.button(text="📋 Выбрать из каталога", callback_data="calc:cat")
  builder.button(text="✍️ Ввести вручную", callback_data="calc:manual")
  builder.adjust(1)
  builder.row(
    InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")
  )
  return builder.as_markup()


def calc_brand_list_kb(brands: list[dict]) -> InlineKeyboardMarkup:
  """Brand picker inside the calc flow (mirrors the catalog brand picker)."""
  builder = InlineKeyboardBuilder()
  for entry in brands:
    brand = entry["brand"]
    count = entry.get("model_count")
    label = f"{brand} ({count})" if count is not None else brand
    builder.button(text=label, callback_data=f"calc:b:{brand}:0")
  builder.adjust(2)
  builder.row(
    InlineKeyboardButton(text="‹ Назад", callback_data="calc:start")
  )
  return builder.as_markup()


def calc_model_page_kb(
  *, brand: str, items: list[dict], page: int, last_page: int
) -> InlineKeyboardMarkup:
  """One page of models in the calc flow; picking leads into the flow."""
  builder = InlineKeyboardBuilder()
  for item in items:
    variant = item.get("variant")
    name = item["model_name"]
    label = f"{name} {variant}" if variant else name
    builder.button(text=label, callback_data=f"calc:m:{item['id']}")
  builder.adjust(1)

  nav: list[InlineKeyboardButton] = []
  if page > 0:
    nav.append(
      InlineKeyboardButton(
        text="‹ Назад", callback_data=f"calc:b:{brand}:{page - 1}"
      )
    )
  nav.append(
    InlineKeyboardButton(
      text=f"{page + 1}/{last_page + 1}", callback_data="cat:noop"
    )
  )
  if page < last_page:
    nav.append(
      InlineKeyboardButton(
        text="Далее ›", callback_data=f"calc:b:{brand}:{page + 1}"
      )
    )
  builder.row(*nav)
  builder.row(
    InlineKeyboardButton(text="‹ К брендам", callback_data="calc:cat")
  )
  return builder.as_markup()


def calc_quantity_kb() -> InlineKeyboardMarkup:
  """Quantity picker 1..5 (FREE hard cap is 5 of one model)."""
  builder = InlineKeyboardBuilder()
  for n in range(1, 6):
    builder.button(text=str(n), callback_data=f"calc:qty:{n}")
  builder.adjust(5)
  builder.row(
    InlineKeyboardButton(text="‹ Назад", callback_data="calc:start")
  )
  return builder.as_markup()


def calc_price_kb(saved_price: str | None) -> InlineKeyboardMarkup:
  """Power-price step: reuse saved price (if any) or enter a new one."""
  builder = InlineKeyboardBuilder()
  if saved_price is not None:
    builder.button(
      text=f"💾 Сохранённая: {saved_price} USDT/кВт·ч",
      callback_data="calc:price:saved",
    )
  builder.button(text="✍️ Ввести цену", callback_data="calc:price:new")
  builder.adjust(1)
  builder.row(
    InlineKeyboardButton(text="‹ Назад", callback_data="calc:start")
  )
  return builder.as_markup()


def calc_result_kb(*, has_firmware: bool) -> InlineKeyboardMarkup:
  """Buttons under the result screen."""
  builder = InlineKeyboardBuilder()
  builder.button(text="🔁 Пересчитать", callback_data="calc:restart")
  if has_firmware:
    builder.button(text="🔧 Сравнить с прошивкой", callback_data="calc:compare")
  builder.adjust(1)
  builder.row(
    InlineKeyboardButton(text="📋 К каталогу", callback_data="menu:catalog"),
    InlineKeyboardButton(text="🏠 Меню", callback_data="menu:home"),
  )
  return builder.as_markup()


def calc_pro_stub_kb() -> InlineKeyboardMarkup:
  """Soft PRO invite: a 'coming soon' stub button + back to menu."""
  builder = InlineKeyboardBuilder()
  builder.button(text="💎 Открыть PRO", callback_data="calc:pro")
  builder.adjust(1)
  builder.row(
    InlineKeyboardButton(text="🏠 Меню", callback_data="menu:home")
  )
  return builder.as_markup()


def plans_kb(plans: list[dict]) -> InlineKeyboardMarkup:
  """PRO plan picker inside Profile: one button per purchasable plan.

  ``plans`` are the API's PlanOut dicts; only plans with a real Stars price are
  offered (the FREE plan is not purchasable). Prices come from the API."""
  builder = InlineKeyboardBuilder()
  for plan in plans:
    price = plan.get("price_stars") or 0
    if price <= 0:
      continue
    title = plan.get("title", plan.get("code", "PRO"))
    builder.button(
      text=f"{title} — {price} ⭐",
      callback_data=f"plan:buy:{plan['code']}",
    )
  builder.adjust(1)
  builder.row(
    InlineKeyboardButton(text="‹ В профиль", callback_data="profile:open")
  )
  return builder.as_markup()


def qr_confirm() -> InlineKeyboardMarkup:
  """Confirm / reject keyboard for a QR-login request."""
  return InlineKeyboardMarkup(
    inline_keyboard=[
      [
        InlineKeyboardButton(
          text="✅ Подтвердить вход", callback_data="qr:approve"
        ),
        InlineKeyboardButton(text="❌ Отклонить", callback_data="qr:reject"),
      ]
    ]
  )
