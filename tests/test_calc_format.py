"""Unit tests for the dependency-free result-screen formatting.

``bot.calc_format`` imports no aiogram, so these run in CI without it."""
from bot.calc_format import (
    format_limit_reached,
    format_result_screen,
)


def _result() -> dict:
    return {
        "btc_per_day": "0.00007858",
        "pool_revenue_usdt_day": "4.66",
        "power_cost_day": "3.90",
        "net_profit_day": "0.7676",
        "net_profit_month": "23.03",
        "net_profit_year": "280.20",
        "roi_days": "2605.27",
        "break_even_power_price": "0.0597",
        "btc_price_usdt": "60000",
        "input": {"power_price": "0.05"},
    }


def _funnel(stage: str, **over) -> dict:
    base = {
        "is_pro": stage == "pro",
        "stage": stage,
        "calc_index": 1,
        "intro_left": 4,
        "daily_left": None,
        "intro_spent": False,
        "pro_hint": None if stage == "pro" else "🔒 hint",
    }
    base.update(over)
    return base


def test_screen_has_title_and_quantity():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel("local_full"),
        title="Antminer S19",
        quantity=3,
    )
    assert "Antminer S19" in text
    assert "× 3" in text


def test_screen_shows_income_and_net_profit():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel("local_full"),
        title="X",
        quantity=1,
    )
    assert "Доход" in text
    assert "Чистая прибыль" in text
    assert "в месяц" in text
    assert "в год" in text


def test_local_full_locks_payback_for_free():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel("local_full"),
        title="X",
        quantity=1,
    )
    assert "🔒 в локальной валюте — в PRO" in text
    assert "окупаемость" in text


def test_blurred_stage_blurs_local_currency():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel("local_blurred", calc_index=4),
        title="X",
        quantity=1,
    )
    assert "▒▒▒" in text
    assert "🔒 в PRO" in text


def test_usdt_only_progress_line():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel(
            "usdt_only",
            intro_spent=True,
            calc_index=6,
            daily_left=2,
            pro_hint="invite",
        ),
        title="X",
        quantity=1,
    )
    assert "Сегодня осталось расчётов: 2 из 3" in text
    assert "▒▒▒" not in text  # no blurred local line in usdt_only


def test_intro_progress_line():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel("local_full", calc_index=2),
        title="X",
        quantity=1,
    )
    assert "Ознакомительный расчёт 2 из 5" in text


def test_pro_has_no_locks_or_blur():
    text = format_result_screen(
        result=_result(),
        funnel=_funnel("pro", is_pro=True),
        title="X",
        quantity=1,
    )
    assert "🔒" not in text
    assert "▒▒▒" not in text


def test_limit_reached_is_warm():
    text = format_limit_reached({"pro_hint": "приходите завтра"})
    assert "Лимит" in text
    assert "PRO" in text
    assert "приходите завтра" in text
