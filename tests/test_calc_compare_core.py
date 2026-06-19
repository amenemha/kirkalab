from decimal import Decimal

from app.services.calc.core import MarketData, MiningInput, compare

MARKET = MarketData(
    btc_price_usdt=Decimal("60000"),
    network_difficulty=Decimal("80000000000000"),
    block_reward_btc=Decimal("3.125"),
)


def _inp(hashrate, power_w, quantity=1):
    return MiningInput(
        hashrate_ths=Decimal(str(hashrate)),
        power_w=power_w,
        quantity=quantity,
        power_price=Decimal("0.05"),
    )


def test_overclock_raises_hashrate_and_power():
    base = _inp(140, 3010)
    custom = _inp(158, 3620)
    result = compare(base, custom, MARKET)

    assert result.delta_hashrate == Decimal("18")
    assert result.delta_power_w == Decimal("610")
    # More hashrate at this price/difficulty wins despite higher draw.
    assert result.delta_profit_day > 0
    assert "разгон" in result.economy_note


def test_undervolt_lowers_power_and_saves_electricity():
    base = _inp(140, 3010)
    custom = _inp(134, 2520)  # less power, slightly less hashrate
    result = compare(base, custom, MARKET)

    assert result.delta_power_w == Decimal("-490")
    # Power cost goes down (negative delta) — electricity savings.
    assert result.delta_power_cost_day < 0
    assert "андервольт" in result.economy_note
    # Efficiency (J/TH) improves -> lower -> negative delta.
    assert result.delta_efficiency_j_per_th < 0


def test_delta_profit_equals_custom_minus_base():
    base = _inp(140, 3010)
    custom = _inp(158, 3620)
    result = compare(base, custom, MARKET)
    assert result.delta_profit_day == (
        result.custom.net_profit_day - result.base.net_profit_day
    )


def test_quantity_scales_deltas():
    base = _inp(100, 3000, quantity=3)
    custom = _inp(120, 3400, quantity=3)
    result = compare(base, custom, MARKET)
    assert result.delta_hashrate == Decimal("60")  # (120-100)*3
    assert result.delta_power_w == Decimal("1200")  # (3400-3000)*3
