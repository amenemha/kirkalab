from decimal import Decimal

import pytest

from app.services.calc.core import MarketData, MiningInput, calculate


def _market() -> MarketData:
    # Fixed market data so the expected numbers are deterministic.
    return MarketData(
        btc_price_usdt=Decimal("60000"),
        network_difficulty=Decimal("80000000000000"),
        block_reward_btc=Decimal("3.125"),
    )


def test_known_result_single_unit():
    inp = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
        pool_fee_pct=Decimal("1.0"),
        uptime_pct=Decimal("100"),
        hardware_cost=Decimal("2000"),
    )
    r = calculate(inp, _market())

    # btc_per_day = 100*1e12*86400*3.125 / (8e13 * 2^32)
    assert float(r.btc_per_day) == pytest.approx(7.858034e-5, rel=1e-5)
    assert float(r.gross_revenue_usdt_day) == pytest.approx(4.71482, rel=1e-5)
    assert float(r.pool_revenue_usdt_day) == pytest.approx(4.66767, rel=1e-5)
    # power: 3250 * 24 / 1000 = 78 kWh * 0.05 = 3.90
    assert r.power_cost_day == Decimal("3.90")
    assert float(r.net_profit_day) == pytest.approx(0.767672, rel=1e-5)
    assert float(r.net_profit_month) == pytest.approx(float(r.net_profit_day) * 30, rel=1e-9)
    assert float(r.net_profit_year) == pytest.approx(float(r.net_profit_day) * 365, rel=1e-9)
    assert float(r.roi_days) == pytest.approx(2605.278, rel=1e-4)
    assert float(r.break_even_power_price) == pytest.approx(0.0598419, rel=1e-5)
    # Transparency fields echoed.
    assert r.btc_price_usdt == Decimal("60000")
    assert r.network_difficulty == Decimal("80000000000000")


def test_quantity_scales_revenue_and_power_linearly():
    base = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
    )
    scaled = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=4,
        power_price=Decimal("0.05"),
    )
    r1 = calculate(base, _market())
    r4 = calculate(scaled, _market())
    assert r4.btc_per_day == r1.btc_per_day * 4
    assert r4.power_cost_day == r1.power_cost_day * 4
    assert float(r4.net_profit_day) == pytest.approx(float(r1.net_profit_day) * 4, rel=1e-9)


def test_uptime_reduces_revenue_and_power():
    full = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
        uptime_pct=Decimal("100"),
    )
    half = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
        uptime_pct=Decimal("50"),
    )
    rf = calculate(full, _market())
    rh = calculate(half, _market())
    assert rh.btc_per_day == rf.btc_per_day / 2
    assert rh.power_cost_day == rf.power_cost_day / 2


def test_roi_none_when_no_hardware_cost():
    inp = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
    )
    r = calculate(inp, _market())
    assert r.roi_days is None


def test_roi_none_when_unprofitable():
    inp = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("5"),  # very high power price -> negative profit
        hardware_cost=Decimal("2000"),
    )
    r = calculate(inp, _market())
    assert r.net_profit_day < 0
    assert r.roi_days is None


def test_zero_difficulty_raises():
    inp = MiningInput(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
    )
    bad = MarketData(
        btc_price_usdt=Decimal("60000"),
        network_difficulty=Decimal("0"),
        block_reward_btc=Decimal("3.125"),
    )
    with pytest.raises(ValueError):
        calculate(inp, bad)
