"""Pure BTC SHA-256 mining profitability core.

This module has **no** dependency on FastAPI, aiogram, the database, or any I/O.
It takes plain inputs and returns a plain result so it can be called in-process
from the API, the Telegram bot, or tests with deterministic market data.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext

# Wide precision so intermediate hashrate * 1e12 terms keep their significance.
getcontext().prec = 50

BLOCKS_PER_DAY = Decimal(144)
HASHES_PER_THS = Decimal(10) ** 12  # 1 TH/s = 1e12 H/s
TWO_POW_32 = Decimal(4294967296)  # 2 ** 32
SECONDS_PER_DAY = Decimal(86400)
DEFAULT_BLOCK_REWARD_BTC = Decimal("3.125")


@dataclass(frozen=True)
class MiningInput:
    """Validated calc inputs. Numeric money/specs use Decimal, never float."""

    hashrate_ths: Decimal
    power_w: int
    quantity: int
    power_price: Decimal  # USDT per kWh
    pool_fee_pct: Decimal = Decimal("1.0")
    uptime_pct: Decimal = Decimal("100")
    hardware_cost: Decimal | None = None  # total cost for the whole fleet


@dataclass(frozen=True)
class MarketData:
    """Market inputs sourced from the latest market snapshot."""

    btc_price_usdt: Decimal
    network_difficulty: Decimal
    block_reward_btc: Decimal = DEFAULT_BLOCK_REWARD_BTC


@dataclass(frozen=True)
class MiningResult:
    btc_per_day: Decimal
    gross_revenue_usdt_day: Decimal
    pool_revenue_usdt_day: Decimal
    power_cost_day: Decimal
    net_profit_day: Decimal
    net_profit_month: Decimal
    net_profit_year: Decimal
    roi_days: Decimal | None
    break_even_power_price: Decimal | None
    # Echoed for transparency.
    btc_price_usdt: Decimal
    network_difficulty: Decimal
    block_reward_btc: Decimal


def calculate(inp: MiningInput, market: MarketData) -> MiningResult:
    """Compute profitability. Never divides by zero."""
    uptime = inp.uptime_pct / Decimal(100)

    # btc_per_day for a single unit at full reward, scaled by quantity below.
    if market.network_difficulty <= 0:
        raise ValueError("network_difficulty must be positive")

    btc_per_day_unit = (
        inp.hashrate_ths
        * HASHES_PER_THS
        * SECONDS_PER_DAY
        * market.block_reward_btc
    ) / (market.network_difficulty * TWO_POW_32)

    btc_per_day = btc_per_day_unit * inp.quantity * uptime

    gross_revenue_usdt_day = btc_per_day * market.btc_price_usdt
    pool_revenue_usdt_day = gross_revenue_usdt_day * (
        Decimal(1) - inp.pool_fee_pct / Decimal(100)
    )

    power_kwh_day = (
        Decimal(inp.power_w) * inp.quantity * Decimal(24) * uptime
    ) / Decimal(1000)
    power_cost_day = power_kwh_day * inp.power_price

    net_profit_day = pool_revenue_usdt_day - power_cost_day
    net_profit_month = net_profit_day * Decimal(30)
    net_profit_year = net_profit_day * Decimal(365)

    roi_days: Decimal | None = None
    if inp.hardware_cost is not None and net_profit_day > 0:
        roi_days = inp.hardware_cost / net_profit_day

    break_even_power_price: Decimal | None = None
    if power_kwh_day > 0:
        break_even_power_price = pool_revenue_usdt_day / power_kwh_day

    return MiningResult(
        btc_per_day=btc_per_day,
        gross_revenue_usdt_day=gross_revenue_usdt_day,
        pool_revenue_usdt_day=pool_revenue_usdt_day,
        power_cost_day=power_cost_day,
        net_profit_day=net_profit_day,
        net_profit_month=net_profit_month,
        net_profit_year=net_profit_year,
        roi_days=roi_days,
        break_even_power_price=break_even_power_price,
        btc_price_usdt=market.btc_price_usdt,
        network_difficulty=market.network_difficulty,
        block_reward_btc=market.block_reward_btc,
    )
