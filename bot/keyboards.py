"""Inline keyboards for the Kirkalab bot (Rapira-style navigation).

Callback data uses a stable ``menu:<action>`` namespace so new menu items
can be added without touching unrelated handlers.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.menu_items import MAIN_MENU_ITEMS, STUB_ACTIONS

__all__ = [
  "MAIN_MENU_ITEMS",
  "STUB_ACTIONS",
  "PAGE_SIZE",
  "main_menu",
  "back_to_menu",
  "qr_confirm",
  "brand_list_kb",
  "model_page_kb",
  "device_card_kb",
  "firmware_back_kb",
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
