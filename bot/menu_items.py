"""Main-menu definition (pure data, no aiogram dependency).

Separated from ``keyboards`` so the menu structure can be unit-tested in
environments without the Telegram libraries installed. To add a menu item,
append to ``MAIN_MENU_ITEMS``; add its action to ``STUB_ACTIONS`` until it
is implemented.
"""
from __future__ import annotations

# (callback action, button label). Order defines on-screen layout.
MAIN_MENU_ITEMS: list[tuple[str, str]] = [
  ("profile", "👤 Профиль"),
  ("calculator", "🧮 Калькулятор ASIC"),
  ("reports", "📊 Мои отчёты"),
  ("plan", "💎 Тариф"),
  ("help", "ℹ️ Помощь"),
]

# Actions that are not implemented yet — handled by a "coming soon" stub.
STUB_ACTIONS: frozenset[str] = frozenset({"calculator", "reports", "plan"})
