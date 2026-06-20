"""HTTP provider for fiat FX rates.

USDT is the anchor. CoinGecko's ``simple/price`` endpoint returns the price of
Tether in many ``vs_currencies`` at once, which is exactly ``1 USDT = ? fiat``
for each requested fiat. We reuse the public, key-free CoinGecko endpoint that
the market layer already depends on rather than adding another FX provider.

Each call has a hard timeout and a small number of retries; transport/HTTP/parse
errors raise ``FxFetchError`` which the service turns into a fallback to the last
persisted ``fx_rates`` row (graceful degradation).
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from app.core.config import get_settings

# The crypto anchor every stored rate is based on. Tether tracks USD ≈ 1:1, so
# USDT→fiat is a usable stand-in for USD→fiat for display purposes.
ANCHOR = "USDT"
# CoinGecko's id for the anchor coin.
_ANCHOR_COINGECKO_ID = "tether"


class FxFetchError(RuntimeError):
    """Raised when upstream FX data cannot be fetched."""


def _get_json(client: httpx.Client, url: str, retries: int) -> dict:
    last_exc: Exception | None = None
    for _ in range(retries + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError = bad JSON
            last_exc = exc
    raise FxFetchError(f"failed to fetch FX rates: {last_exc}")


def fetch_anchor_rates(fiat_codes: list[str]) -> dict[str, Decimal]:
    """Return ``{fiat_code: rate}`` where ``1 USDT = rate fiat``.

    ``fiat_codes`` are ISO-4217 codes (e.g. ``["USD", "RUB"]``); CoinGecko's
    ``vs_currencies`` are lowercase. Codes the upstream does not know are simply
    absent from the result (the caller decides how to handle a missing pair).
    """
    if not fiat_codes:
        return {}
    settings = get_settings()
    retries = settings.fx_http_retries
    timeout = httpx.Timeout(settings.fx_http_timeout_seconds)
    vs = ",".join(c.lower() for c in fiat_codes)
    url = (
        f"{settings.coingecko_base_url}/api/v3/simple/price"
        f"?ids={_ANCHOR_COINGECKO_ID}&vs_currencies={vs}"
    )
    with httpx.Client(timeout=timeout) as client:
        data = _get_json(client, url, retries)
    try:
        payload = data[_ANCHOR_COINGECKO_ID]
    except (KeyError, TypeError) as exc:
        raise FxFetchError(f"unexpected CoinGecko FX payload: {data}") from exc

    rates: dict[str, Decimal] = {}
    for code in fiat_codes:
        value = payload.get(code.lower())
        if value is None:
            continue
        rate = Decimal(str(value))
        if rate <= 0:
            continue
        rates[code.upper()] = rate
    if not rates:
        raise FxFetchError(f"no usable FX rates in payload: {payload}")
    return rates
