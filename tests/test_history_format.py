"""Unit tests for the dependency-free history-screen formatting + pagination.

``bot.history_format`` imports no aiogram, so these run in CI without it."""
from bot.history_format import (
    PAGE_SIZE,
    clamp_page,
    format_datetime,
    format_detail_screen,
    format_empty_screen,
    format_list_item,
    format_list_screen,
    page_count,
    page_offset,
)


def _item(**over) -> dict:
    base = {
        "id": 1,
        "device_name": "Antminer S19 Pro",
        "quantity": 2,
        "currency": "USDT",
        "hashrate_ths": "110.00",
        "power_w": 3250,
        "power_price": "0.0500",
        "net_profit_day_usdt": "1.23456789",
        "net_profit_month_usdt": "37.00000000",
        "created_at": "2026-06-20T10:30:00+00:00",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Pagination math.
# --------------------------------------------------------------------------- #
def test_page_count_basic():
    assert page_count(0) == 1
    assert page_count(1) == 1
    assert page_count(PAGE_SIZE) == 1
    assert page_count(PAGE_SIZE + 1) == 2
    assert page_count(PAGE_SIZE * 3) == 3


def test_clamp_page_bounds():
    # 12 items, page size 5 -> pages 0..2.
    assert clamp_page(-1, 12) == 0
    assert clamp_page(0, 12) == 0
    assert clamp_page(2, 12) == 2
    assert clamp_page(5, 12) == 2


def test_page_offset():
    assert page_offset(0) == 0
    assert page_offset(2) == 2 * PAGE_SIZE


# --------------------------------------------------------------------------- #
# Date formatting.
# --------------------------------------------------------------------------- #
def test_format_datetime():
    assert format_datetime("2026-06-20T10:30:00+00:00") == "20.06.2026 10:30"
    assert format_datetime("2026-06-20T10:30:00Z") == "20.06.2026 10:30"


def test_format_datetime_graceful_on_garbage():
    assert format_datetime("not-a-date") == "not-a-date"
    assert format_datetime(None) == "—"


# --------------------------------------------------------------------------- #
# List item + list screen.
# --------------------------------------------------------------------------- #
def test_list_item_has_date_device_qty_profit():
    line = format_list_item(_item(), index=1)
    assert "1." in line
    assert "20.06.2026 10:30" in line
    assert "Antminer S19 Pro" in line
    assert "× 2" in line
    # Daily profit rounded to 2 dp with thousands grouping.
    assert "1.23 USDT/день" in line


def test_list_item_fallback_device_name():
    line = format_list_item(_item(device_name=None), index=1)
    assert "Оборудование" in line


def test_list_screen_lists_items_with_numbers():
    items = [_item(id=1), _item(id=2, device_name="Whatsminer M30S")]
    text = format_list_screen(
        items, page=0, total=2, is_pro=False, truncated=False
    )
    assert "Мои отчёты" in text
    assert "1." in text and "2." in text
    assert "Whatsminer M30S" in text
    # Single page -> no "Страница" footer.
    assert "Страница" not in text


def test_list_screen_shows_pagination_footer_when_multipage():
    items = [_item(id=i) for i in range(1, PAGE_SIZE + 1)]
    text = format_list_screen(
        items, page=0, total=PAGE_SIZE * 2, is_pro=False, truncated=False
    )
    assert "Страница 1 из 2" in text


def test_list_screen_numbers_continue_on_second_page():
    items = [_item(id=i) for i in range(PAGE_SIZE + 1, PAGE_SIZE + 3)]
    text = format_list_screen(
        items, page=1, total=PAGE_SIZE + 2, is_pro=False, truncated=False
    )
    # Second page numbering starts at PAGE_SIZE+1.
    assert f"{PAGE_SIZE + 1}." in text


def test_list_screen_pro_hint_only_for_truncated_free():
    items = [_item()]
    free = format_list_screen(
        items, page=0, total=1, is_pro=False, truncated=True, retention_days=3
    )
    assert "PRO" in free
    assert "3 дн." in free

    # PRO never sees the hint, even if truncated were (wrongly) set.
    pro = format_list_screen(
        items, page=0, total=1, is_pro=True, truncated=True, retention_days=3
    )
    assert "PRO" not in pro

    # FREE not truncated -> no hint.
    free_full = format_list_screen(
        items, page=0, total=1, is_pro=False, truncated=False, retention_days=3
    )
    assert "PRO" not in free_full


# --------------------------------------------------------------------------- #
# Empty state.
# --------------------------------------------------------------------------- #
def test_empty_screen_is_friendly():
    text = format_empty_screen()
    assert "нет сохранённых расчётов" in text


# --------------------------------------------------------------------------- #
# Detail screen.
# --------------------------------------------------------------------------- #
def test_detail_screen_shows_params_and_results():
    text = format_detail_screen(_item())
    assert "Antminer S19 Pro" in text
    assert "× 2" in text
    assert "20.06.2026 10:30" in text
    assert "110" in text  # hashrate, trailing zeros trimmed
    assert "3250" in text  # power
    assert "0.0500 USDT/кВт·ч" in text
    assert "в день: 1.23 USDT" in text
    assert "в месяц: 37.00 USDT" in text


def test_detail_screen_handles_missing_month():
    text = format_detail_screen(_item(net_profit_month_usdt=None))
    assert "в месяц" not in text
    assert "в день" in text
