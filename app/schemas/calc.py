"""Request/response schemas for the calc API.

Free-mode validation is strict. Setting ``premium=True`` relaxes the quantity
cap and the upper bounds so power users can model large or unusual fleets.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Strict free-mode bounds.
FREE_HASHRATE_MIN = Decimal("0.1")
FREE_HASHRATE_MAX = Decimal("2000")
FREE_POWER_MIN = 1
FREE_POWER_MAX = 20000
FREE_QUANTITY_MAX = 5
FREE_POWER_PRICE_MIN = Decimal("0")
FREE_POWER_PRICE_MAX = Decimal("5")
FREE_POOL_FEE_MIN = Decimal("0")
FREE_POOL_FEE_MAX = Decimal("10")
FREE_UPTIME_MIN = Decimal("1")
FREE_UPTIME_MAX = Decimal("100")


class CalcRequest(BaseModel):
    hashrate_ths: Decimal
    power_w: int
    quantity: int = 1
    power_price: Decimal
    pool_fee_pct: Decimal = Decimal("1.0")
    uptime_pct: Decimal = Decimal("100")
    hardware_cost: Decimal | None = None
    premium: bool = False

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_ranges(self) -> "CalcRequest":
        def fail(param: str, message: str) -> None:
            raise ValueError(f"{param}: {message}")

        # Always-on sanity floors (apply to premium too: no zero/negative).
        if self.hashrate_ths <= 0:
            fail("hashrate_ths", "must be greater than 0")
        if self.power_w < 1:
            fail("power_w", "must be at least 1 W")
        if self.quantity < 1:
            fail("quantity", "must be at least 1")
        if self.power_price < 0:
            fail("power_price", "must not be negative")
        if not (FREE_POOL_FEE_MIN <= self.pool_fee_pct <= FREE_POOL_FEE_MAX):
            fail(
                "pool_fee_pct",
                f"must be between {FREE_POOL_FEE_MIN} and {FREE_POOL_FEE_MAX}",
            )
        if not (FREE_UPTIME_MIN <= self.uptime_pct <= FREE_UPTIME_MAX):
            fail(
                "uptime_pct",
                f"must be between {FREE_UPTIME_MIN} and {FREE_UPTIME_MAX}",
            )
        if self.hardware_cost is not None and self.hardware_cost < 0:
            fail("hardware_cost", "must not be negative")

        # Strict free-mode bounds (relaxed when premium=True).
        if not self.premium:
            if not (FREE_HASHRATE_MIN <= self.hashrate_ths <= FREE_HASHRATE_MAX):
                fail(
                    "hashrate_ths",
                    f"must be between {FREE_HASHRATE_MIN} and {FREE_HASHRATE_MAX} TH/s",
                )
            if not (FREE_POWER_MIN <= self.power_w <= FREE_POWER_MAX):
                fail(
                    "power_w",
                    f"must be between {FREE_POWER_MIN} and {FREE_POWER_MAX} W",
                )
            if self.quantity > FREE_QUANTITY_MAX:
                fail(
                    "quantity",
                    f"must be between 1 and {FREE_QUANTITY_MAX} in free mode",
                )
            if not (FREE_POWER_PRICE_MIN <= self.power_price <= FREE_POWER_PRICE_MAX):
                fail(
                    "power_price",
                    f"must be between {FREE_POWER_PRICE_MIN} and "
                    f"{FREE_POWER_PRICE_MAX} USDT/kWh",
                )
        return self


class CalcResponse(BaseModel):
    btc_per_day: Decimal
    gross_revenue_usdt_day: Decimal
    pool_revenue_usdt_day: Decimal
    power_cost_day: Decimal
    net_profit_day: Decimal
    net_profit_month: Decimal
    net_profit_year: Decimal
    roi_days: Decimal | None = None
    break_even_power_price: Decimal | None = None
    # Transparency: the market data the result was computed against.
    btc_price_usdt: Decimal
    network_difficulty: Decimal
    block_reward_btc: Decimal
    market_captured_at: str | None = None

    input: CalcRequest = Field(...)
