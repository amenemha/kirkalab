"""Bot-side billing keyboard tests (guarded for environments without aiogram)."""
import pytest

pytest.importorskip("aiogram")

from bot.keyboards import plans_kb


def _texts(markup):
    return [btn.text for row in markup.inline_keyboard for btn in row]


def _callbacks(markup):
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
    ]


def test_plans_kb_lists_only_purchasable_plans():
    plans = [
        {"code": "free", "title": "Free", "price_stars": 0},
        {"code": "pro_monthly", "title": "PRO на месяц", "price_stars": 250},
        {"code": "pro_yearly", "title": "PRO на год", "price_stars": 2500},
    ]
    markup = plans_kb(plans)
    callbacks = _callbacks(markup)
    # FREE is skipped; both PRO plans + a back button are present.
    assert "plan:buy:pro_monthly" in callbacks
    assert "plan:buy:pro_yearly" in callbacks
    assert "plan:buy:free" not in callbacks
    assert "profile:open" in callbacks


def test_plans_kb_shows_star_price():
    plans = [{"code": "pro_monthly", "title": "PRO на месяц", "price_stars": 250}]
    texts = _texts(plans_kb(plans))
    assert any("250 ⭐" in t for t in texts)
