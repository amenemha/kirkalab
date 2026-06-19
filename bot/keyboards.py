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
  "main_menu",
  "back_to_menu",
  "qr_confirm",
]


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
