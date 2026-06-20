"""Tests for the calc-flow inline keyboards: callback-data shape and the
quantity cap. Skipped when aiogram is unavailable (CI without aiogram)."""
import pytest

pytest.importorskip("aiogram")  # bot.keyboards builds aiogram InlineKeyboardMarkup

from bot.keyboards import (
    calc_brand_list_kb,
    calc_model_page_kb,
    calc_price_kb,
    calc_quantity_kb,
    calc_result_kb,
    calc_start_kb,
    export_upsell_kb,
)


def _all_callbacks(markup) -> list[str]:
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    ]


def test_start_kb_offers_catalog_and_manual():
    cbs = _all_callbacks(calc_start_kb())
    assert "calc:cat" in cbs
    assert "calc:manual" in cbs


def test_quantity_kb_is_one_to_five():
    cbs = _all_callbacks(calc_quantity_kb())
    assert [f"calc:qty:{n}" for n in range(1, 6)] == [
        c for c in cbs if c.startswith("calc:qty:")
    ]


def test_model_pick_uses_calc_namespace():
    items = [{"id": 7, "model_name": "S19", "variant": "95T"}]
    cbs = _all_callbacks(
        calc_model_page_kb(brand="Bitmain", items=items, page=0, last_page=0)
    )
    assert "calc:m:7" in cbs
    assert all(len(c.encode()) <= 64 for c in cbs)


def test_price_kb_shows_saved_when_present():
    with_saved = _all_callbacks(calc_price_kb("0.05"))
    assert "calc:price:saved" in with_saved
    assert "calc:price:new" in with_saved
    without = _all_callbacks(calc_price_kb(None))
    assert "calc:price:saved" not in without


def test_result_kb_firmware_toggle():
    assert "calc:compare" in _all_callbacks(calc_result_kb(has_firmware=True))
    assert "calc:compare" not in _all_callbacks(
        calc_result_kb(has_firmware=False)
    )


def test_result_kb_export_button_when_run_id():
    with_run = _all_callbacks(calc_result_kb(has_firmware=False, run_id=42))
    assert "calc:xlsx:42" in with_run
    without = _all_callbacks(calc_result_kb(has_firmware=False))
    assert not any(c.startswith("calc:xlsx:") for c in without)


def test_export_upsell_kb_links_to_plan_and_back():
    cbs = _all_callbacks(export_upsell_kb("calc:restart"))
    assert "profile:plan" in cbs
    assert "calc:restart" in cbs


def test_brand_list_kb_back_to_start():
    cbs = _all_callbacks(calc_brand_list_kb([{"brand": "Bitmain", "model_count": 3}]))
    assert "calc:b:Bitmain:0" in cbs
    assert "calc:start" in cbs
