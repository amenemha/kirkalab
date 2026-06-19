"""HTTP providers for external market data.

CoinGecko -> BTC price in USDT.
mempool.space -> network difficulty and current block reward (subsidy).

Both are public, key-free endpoints. Each call has a hard timeout and a small
number of retries; transport/HTTP errors raise ``MarketFetchError`` which the
service layer turns into a fallback to the last good snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.core.config import get_settings


class MarketFetchError(RuntimeError):
    """Raised when upstream market data cannot be fetched."""


@dataclass(frozen=True)
class RawMarketData:
    price_usdt: Decimal
    network_difficulty: Decimal
    block_reward_btc: Decimal


def _get_json(client: httpx.Client, url: str, retries: int) -> dict:
    last_exc: Exception | None = None
    for _ in range(retries + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError = bad JSON
            last_exc = exc
    raise MarketFetchError(f"failed to fetch {url}: {last_exc}")


def fetch_btc_price_usdt(client: httpx.Client, retries: int) -> Decimal:
    settings = get_settings()
    url = (
        f"{settings.coingecko_base_url}/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd"
    )
    data = _get_json(client, url, retries)
    try:
        return Decimal(str(data["bitcoin"]["usd"]))
    except (KeyError, TypeError) as exc:
        raise MarketFetchError(f"unexpected CoinGecko payload: {data}") from exc


def fetch_difficulty_and_reward(
    client: httpx.Client, retries: int
) -> tuple[Decimal, Decimal]:
    settings = get_settings()
    default_reward = Decimal(settings.market_default_block_reward_btc)

    # Network difficulty from the latest difficulty adjustment.
    diff_url = f"{settings.mempool_base_url}/api/v1/difficulty-adjustment"
    diff_data = _get_json(client, diff_url, retries)
    try:
        difficulty = Decimal(str(diff_data["difficulty"]))
    except (KeyError, TypeError) as exc:
        raise MarketFetchError(
            f"unexpected mempool difficulty payload: {diff_data}"
        ) from exc

    # Block reward (subsidy) from the latest block. Sats -> BTC. Falls back to
    # the configured default if the field is missing.
    block_reward = default_reward
    try:
        tip = _get_json(client, f"{settings.mempool_base_url}/api/blocks/tip", retries)
        if isinstance(tip, list) and tip:
            extras = tip[0].get("extras") or {}
            subsidy = extras.get("reward")
            if subsidy is not None:
                # mempool "reward" includes fees; subsidy alone is more stable
                # for projections, so prefer the halving-based default unless
                # we only have the total. Use the explicit subsidy if present.
                subsidy_val = extras.get("subsidy", subsidy)
                block_reward = Decimal(str(subsidy_val)) / Decimal(10**8)
    except MarketFetchError:
        # Reward is non-critical; keep the default.
        block_reward = default_reward

    return difficulty, block_reward


def fetch_market_data() -> RawMarketData:
    settings = get_settings()
    retries = settings.market_http_retries
    timeout = httpx.Timeout(settings.market_http_timeout_seconds)
    with httpx.Client(timeout=timeout) as client:
        price = fetch_btc_price_usdt(client, retries)
        difficulty, reward = fetch_difficulty_and_reward(client, retries)
    return RawMarketData(
        price_usdt=price,
        network_difficulty=difficulty,
        block_reward_btc=reward,
    )
