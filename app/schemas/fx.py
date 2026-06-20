"""Request/response schemas for the FX (currency) internal API.

Money/rate fields are ``Decimal`` so values never pass through float; FastAPI
serializes them as JSON numbers/strings and the bot formats them for display.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CurrencyOut(BaseModel):
    code: str
    symbol: str
    name: str
    decimals: int
    is_fiat: bool

    model_config = ConfigDict(from_attributes=True)


class FxRatesResponse(BaseModel):
    """Current anchor rates ``1 USDT = rate <fiat>`` plus the currency catalog.

    ``rates`` is keyed by fiat code; the anchor (USDT) is implicitly 1 and not
    included. ``stale`` is True when the figures came from the persisted
    fallback rather than a fresh upstream fetch (source was down)."""

    base: str = "USDT"
    rates: dict[str, Decimal]
    currencies: list[CurrencyOut]
    stale: bool = False


class FxConvertRequest(BaseModel):
    amount: Decimal
    from_currency: str = "USDT"
    to_currency: str

    model_config = ConfigDict(extra="forbid")


class FxConvertResponse(BaseModel):
    amount: Decimal
    from_currency: str
    to_currency: str
    converted: Decimal
    rate: Decimal
