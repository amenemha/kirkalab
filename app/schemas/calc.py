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


class CompareRequest(BaseModel):
    """Stock-vs-custom comparison input.

    The base (stock) side is given by ``hashrate_ths`` + ``power_w``. The custom
    side is resolved from, in order of precedence:
    ``user_firmware_build_id`` > ``firmware_preset_id`` > explicit
    ``custom_hashrate_ths`` + ``custom_power_w``. Shared economics (quantity,
    power price, pool fee, uptime) apply to both sides."""

    hashrate_ths: Decimal
    power_w: int
    custom_hashrate_ths: Decimal | None = None
    custom_power_w: int | None = None
    firmware_preset_id: int | None = None
    user_firmware_build_id: int | None = None

    quantity: int = 1
    power_price: Decimal
    pool_fee_pct: Decimal = Decimal("1.0")
    uptime_pct: Decimal = Decimal("100")
    hardware_cost: Decimal | None = None
    premium: bool = False

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate(self) -> "CompareRequest":
        if self.hashrate_ths <= 0:
            raise ValueError("hashrate_ths: must be greater than 0")
        if self.power_w < 1:
            raise ValueError("power_w: must be at least 1 W")
        if self.quantity < 1:
            raise ValueError("quantity: must be at least 1")
        if not self.premium and self.quantity > FREE_QUANTITY_MAX:
            raise ValueError(
                f"quantity: must be between 1 and {FREE_QUANTITY_MAX} in free mode"
            )
        if self.power_price < 0:
            raise ValueError("power_price: must not be negative")
        if not (FREE_POOL_FEE_MIN <= self.pool_fee_pct <= FREE_POOL_FEE_MAX):
            raise ValueError(
                f"pool_fee_pct: must be between {FREE_POOL_FEE_MIN} and "
                f"{FREE_POOL_FEE_MAX}"
            )
        if not (FREE_UPTIME_MIN <= self.uptime_pct <= FREE_UPTIME_MAX):
            raise ValueError(
                f"uptime_pct: must be between {FREE_UPTIME_MIN} and {FREE_UPTIME_MAX}"
            )
        has_custom_source = (
            self.user_firmware_build_id is not None
            or self.firmware_preset_id is not None
            or (self.custom_hashrate_ths is not None and self.custom_power_w is not None)
        )
        if not has_custom_source:
            raise ValueError(
                "custom side required: pass user_firmware_build_id, "
                "firmware_preset_id, or both custom_hashrate_ths and custom_power_w"
            )
        if self.custom_hashrate_ths is not None and self.custom_hashrate_ths <= 0:
            raise ValueError("custom_hashrate_ths: must be greater than 0")
        if self.custom_power_w is not None and self.custom_power_w < 1:
            raise ValueError("custom_power_w: must be at least 1 W")
        return self


class CompareDelta(BaseModel):
    delta_profit_day: Decimal | None = None
    delta_power_w: Decimal | None = None
    delta_power_cost_day: Decimal | None = None
    delta_hashrate: Decimal | None = None
    delta_efficiency_j_per_th: Decimal | None = None
    economy_note: str | None = None
    # True when the caller is not PRO: the delta is withheld behind the gate.
    pro_required: bool = False


class CompareResponse(BaseModel):
    base: CalcResponse
    # Custom-side result. Withheld (null) for non-PRO callers.
    custom: CalcResponse | None = None
    delta: CompareDelta
    market_captured_at: str | None = None
