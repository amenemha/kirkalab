from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.calc import CalcRequest


def _valid(**overrides):
    data = dict(
        hashrate_ths=Decimal("100"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.05"),
    )
    data.update(overrides)
    return data


def test_valid_request_passes():
    req = CalcRequest(**_valid())
    assert req.quantity == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("hashrate_ths", Decimal("0.05")),  # below 0.1
        ("hashrate_ths", Decimal("3000")),  # above 2000
        ("power_w", 0),  # below 1
        ("power_w", 25000),  # above 20000
        ("quantity", 6),  # above free cap 5
        ("power_price", Decimal("6")),  # above 5
        ("pool_fee_pct", Decimal("11")),  # above 10
        ("uptime_pct", Decimal("0")),  # below 1
        ("uptime_pct", Decimal("101")),  # above 100
    ],
)
def test_free_mode_rejects_out_of_range(field, value):
    with pytest.raises(ValidationError) as exc:
        CalcRequest(**_valid(**{field: value}))
    # Error message names the offending parameter.
    assert field in str(exc.value)


def test_premium_relaxes_quantity_and_bounds():
    req = CalcRequest(**_valid(quantity=50, hashrate_ths=Decimal("5000"), premium=True))
    assert req.quantity == 50
    assert req.hashrate_ths == Decimal("5000")


def test_premium_still_rejects_nonpositive():
    with pytest.raises(ValidationError):
        CalcRequest(**_valid(hashrate_ths=Decimal("0"), premium=True))


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CalcRequest(**_valid(unknown_field=123))
