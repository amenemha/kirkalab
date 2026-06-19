"""Tests for the catalog inline keyboards: callback-data stays within
Telegram's 64-byte limit and pagination buttons appear only when needed."""
from bot.keyboards import (
    PAGE_SIZE,
    brand_list_kb,
    device_card_kb,
    firmware_back_kb,
    model_page_kb,
)


def _all_callbacks(markup) -> list[str]:
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    ]


def test_callback_data_within_telegram_limit():
    brands = [{"brand": "MicroBT", "model_count": 45}]
    items = [
        {"id": 999999, "model_name": "WhatsMiner M66S Hydro", "variant": "X"}
    ]
    markups = [
        brand_list_kb(brands),
        model_page_kb(brand="MicroBT", items=items, page=3, last_page=9),
        device_card_kb(brand="MicroBT", model_id=999999, has_firmware=True),
        firmware_back_kb(999999),
    ]
    for markup in markups:
        for data in _all_callbacks(markup):
            assert len(data.encode("utf-8")) <= 64, data


def test_brand_list_shows_counts_and_back():
    markup = brand_list_kb([{"brand": "Bitmain", "model_count": 63}])
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert "Bitmain (63)" in texts
    assert any("меню" in t.lower() for t in texts)


def test_model_page_first_page_has_no_prev():
    items = [{"id": i, "model_name": f"M{i}", "variant": None} for i in range(8)]
    markup = model_page_kb(brand="Bitmain", items=items, page=0, last_page=5)
    cbs = _all_callbacks(markup)
    assert "cat:b:Bitmain:1" in cbs  # next
    assert "cat:b:Bitmain:-1" not in cbs  # no prev on first page


def test_model_page_last_page_has_no_next():
    items = [{"id": 1, "model_name": "M1", "variant": None}]
    markup = model_page_kb(brand="Bitmain", items=items, page=5, last_page=5)
    cbs = _all_callbacks(markup)
    assert "cat:b:Bitmain:4" in cbs  # prev
    assert "cat:b:Bitmain:6" not in cbs  # no next on last page


def test_device_card_hides_firmware_when_none():
    with_fw = _all_callbacks(
        device_card_kb(brand="Bitmain", model_id=5, has_firmware=True)
    )
    without_fw = _all_callbacks(
        device_card_kb(brand="Bitmain", model_id=5, has_firmware=False)
    )
    assert "cat:fw:5" in with_fw
    assert "cat:fw:5" not in without_fw


def test_page_size_is_reasonable():
    assert 1 <= PAGE_SIZE <= 10
