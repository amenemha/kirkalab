"""Pure currency-conversion math (no DB/Redis/HTTP)."""
from decimal import Decimal

import pytest

from app.services.fx.convert import (
    convert_amount,
    cross_rate,
    quantize_money,
)


def test_quantize_to_fiat_two_places():
    assert quantize_money(Decimal("1.005"), 2) == Decimal("1.01")
    assert quantize_money(Decimal("1.004"), 2) == Decimal("1.00")


def test_quantize_zero_decimals():
    assert quantize_money(Decimal("1234.56"), 0) == Decimal("1235")


def test_convert_rounds_to_target_decimals():
    # 10 USDT at 92.5 RUB → 925.00
    assert convert_amount(Decimal("10"), Decimal("92.5"), decimals=2) == Decimal(
        "925.00"
    )


def test_convert_no_float_drift():
    # 0.1 + 0.2 style drift never appears with Decimal.
    out = convert_amount(Decimal("0.1"), Decimal("3"), decimals=2)
    assert out == Decimal("0.30")


def test_convert_high_precision_rate_then_quantize_once():
    # Rate with 8 dp; only the final figure is quantized to 2 dp.
    out = convert_amount(Decimal("100"), Decimal("0.92345678"), decimals=2)
    assert out == Decimal("92.35")


def test_convert_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        convert_amount(Decimal("1"), Decimal("0"), decimals=2)
    with pytest.raises(ValueError):
        convert_amount(Decimal("1"), Decimal("-1"), decimals=2)


def test_cross_rate_via_anchor():
    # 1 USDT = 90 RUB, 1 USDT = 0.9 EUR  →  1 RUB = 0.01 EUR
    rate = cross_rate(base_to_anchor=Decimal("90"), quote_to_anchor=Decimal("0.9"))
    assert rate == Decimal("0.01")


def test_cross_rate_inverse_consistency():
    # RUB→EUR then EUR→RUB should round-trip the amount closely.
    rub_to_eur = cross_rate(
        base_to_anchor=Decimal("90"), quote_to_anchor=Decimal("0.9")
    )
    eur_to_rub = cross_rate(
        base_to_anchor=Decimal("0.9"), quote_to_anchor=Decimal("90")
    )
    amount = Decimal("1000")
    in_eur = convert_amount(amount, rub_to_eur, decimals=2)
    back = convert_amount(in_eur, eur_to_rub, decimals=2)
    assert back == Decimal("1000.00")


def test_cross_rate_rejects_non_positive():
    with pytest.raises(ValueError):
        cross_rate(base_to_anchor=Decimal("0"), quote_to_anchor=Decimal("1"))
    with pytest.raises(ValueError):
        cross_rate(base_to_anchor=Decimal("1"), quote_to_anchor=Decimal("0"))
