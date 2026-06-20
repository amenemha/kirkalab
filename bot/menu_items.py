"""Main-menu definition (pure data, no aiogram dependency).

Separated from ``keyboards`` so the menu structure can be unit-tested in
environments without the Telegram libraries installed. To add a menu item,
append to ``MAIN_MENU_ITEMS``; add its action to ``STUB_ACTIONS`` until it
is implemented.
"""
from __future__ import annotations

# (callback action, button label). Order defines on-screen layout.
# Used by the inline menu helpers (still exercised by tests / fallbacks).
MAIN_MENU_ITEMS: list[tuple[str, str]] = [
  ("profile", "👤 Профиль"),
  ("calculator", "🧮 Рассчитать доходность"),
  ("catalog", "📋 Каталог ASIC"),
  ("reports", "📊 Мои отчёты"),
  ("plan", "💎 Тариф"),
  ("help", "ℹ️ Помощь"),
]

# Actions that are not implemented yet — handled by a "coming soon" stub.
STUB_ACTIONS: frozenset[str] = frozenset({"plan"})

# --------------------------------------------------------------------------- #
# Reply-keyboard main menu (CALC_SPEC §3.2): the persistent "прибитая"
# keyboard under the input field. MAX 4 buttons, laid out 2×2. Tariff/PRO and
# Help live inside Profile (inline), NOT as top-level buttons, so the tariff
# never "маячит". Each entry is (action, button text); the text is what the
# user taps, matched verbatim by the menu router.
# --------------------------------------------------------------------------- #
REPLY_MENU_ITEMS: list[tuple[str, str]] = [
  ("calculator", "🧮 Калькулятор"),
  ("catalog", "📋 Каталог ASIC"),
  ("reports", "📊 Мои отчёты"),
  ("profile", "👤 Профиль"),
]

# Fast text -> action lookup for the reply-keyboard handler.
REPLY_MENU_BY_TEXT: dict[str, str] = {text: action for action, text in REPLY_MENU_ITEMS}
