"""Tests for the history inline keyboards: callback-data shape, pagination
visibility, PRO hint button. Skipped when aiogram is unavailable (CI)."""
import pytest

pytest.importorskip("aiogram")  # bot.keyboards builds aiogram markups

from bot.keyboards import (
    history_detail_kb,
    history_empty_kb,
    history_list_kb,
)


def _callbacks(markup) -> list[str]:
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    ]


def _items(n: int) -> list[dict]:
    return [{"id": i} for i in range(1, n + 1)]


def test_list_kb_open_buttons_carry_run_ids():
    cbs = _callbacks(history_list_kb(_items(3), page=0, last_page=0))
    assert "hist:open:1" in cbs
    assert "hist:open:2" in cbs
    assert "hist:open:3" in cbs
    assert "menu:home" in cbs


def test_list_kb_no_pagination_on_single_page():
    cbs = _callbacks(history_list_kb(_items(2), page=0, last_page=0))
    assert not any(c.startswith("hist:p:") for c in cbs)


def test_list_kb_pagination_arrows_on_multipage():
    # Middle page -> both arrows.
    cbs = _callbacks(history_list_kb(_items(5), page=1, last_page=2))
    assert "hist:p:0" in cbs
    assert "hist:p:2" in cbs


def test_list_kb_first_page_has_no_back_arrow():
    cbs = _callbacks(history_list_kb(_items(5), page=0, last_page=2))
    assert "hist:p:1" in cbs  # forward
    assert "hist:p:-1" not in cbs  # no back arrow on first page


def test_list_kb_pro_hint_button_when_requested():
    with_pro = _callbacks(
        history_list_kb(_items(1), page=0, last_page=0, show_pro=True)
    )
    assert "profile:plan" in with_pro

    without = _callbacks(
        history_list_kb(_items(1), page=0, last_page=0, show_pro=False)
    )
    assert "profile:plan" not in without


def test_detail_kb_has_back_to_list_and_menu():
    cbs = _callbacks(history_detail_kb())
    assert "hist:list" in cbs
    assert "menu:home" in cbs


def test_detail_kb_export_button_when_run_id():
    cbs = _callbacks(history_detail_kb(7))
    assert "hist:xlsx:7" in cbs
    assert "hist:list" in cbs
    # No run id -> no export button.
    assert not any(c.startswith("hist:xlsx:") for c in _callbacks(history_detail_kb()))


def test_empty_kb_has_calculator_cta():
    cbs = _callbacks(history_empty_kb())
    assert "menu:calculator" in cbs
    assert "menu:home" in cbs
