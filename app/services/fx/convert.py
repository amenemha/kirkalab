"""Pure currency-conversion math.

No FastAPI, aiogram, DB, Redis or HTTP imports — it takes a Decimal amount and a
rate and returns a Decimal, so the rounding/precision rules are unit-testable in
isolation and shared by the service and (indirectly) the bot.

All money is ``Decimal``; never float. The conversion rounds to the target
currency's ``decimals`` using banker's-safe ``ROUND_HALF_UP`` (the convention
users expect for money), and cross-rates are derived from the USDT anchor so the
intermediate keeps full precision before the single final quantize.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def quantize_money(amount: Decimal, decimals: int) -> Decimal:
    """Round ``amount`` to ``decimals`` fractional digits (ROUND_HALF_UP)."""
    if decimals < 0:
        decimals = 0
    quant = Decimal(1).scaleb(-decimals)  # e.g. decimals=2 -> Decimal("0.01")
    return amount.quantize(quant, rounding=ROUND_HALF_UP)


def convert_amount(
    amount: Decimal, rate: Decimal, *, decimals: int
) -> Decimal:
    """Convert ``amount`` (in the base currency) by ``rate`` (base→quote).

    ``rate`` is ``1 base = rate quote``. The multiplication keeps full Decimal
    precision; only the final result is quantized to the quote currency's
    ``decimals``. Raises ``ValueError`` on a non-positive rate so a bad/missing
    rate can never silently produce a zero or negative figure."""
    if rate <= 0:
        raise ValueError("rate must be positive")
    return quantize_money(amount * rate, decimals)


def cross_rate(base_to_anchor: Decimal, quote_to_anchor: Decimal) -> Decimal:
    """Derive ``1 base = ? quote`` from two anchor rates.

    Given ``base_to_anchor`` (``1 anchor = base``) and ``quote_to_anchor``
    (``1 anchor = quote``), the base→quote rate is ``quote_to_anchor /
    base_to_anchor``. Used to convert between two fiats via the USDT anchor
    without storing every fiat↔fiat pair."""
    if base_to_anchor <= 0:
        raise ValueError("base_to_anchor must be positive")
    if quote_to_anchor <= 0:
        raise ValueError("quote_to_anchor must be positive")
    return quote_to_anchor / base_to_anchor
