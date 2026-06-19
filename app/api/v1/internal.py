import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services.market.service import MarketUnavailableError, refresh_market_data

router = APIRouter(prefix="/internal", tags=["internal"])

settings = get_settings()


def _require_bot_secret(x_bot_secret: str | None) -> None:
    expected = settings.bot_internal_secret
    if (
        not expected
        or not x_bot_secret
        or not secrets.compare_digest(x_bot_secret, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bot secret"
        )


@router.post("/refresh-market")
def refresh_market(
    db: Session = Depends(get_db),
    x_bot_secret: str | None = Header(default=None, alias="X-Bot-Secret"),
) -> dict:
    """Force-refresh the market snapshot. Intended for a scheduler/cron or the
    bot to call periodically so the calc core never blocks on a cold cache."""
    _require_bot_secret(x_bot_secret)
    try:
        data, captured_at = refresh_market_data(db)
    except MarketUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "status": "ok",
        "captured_at": captured_at.isoformat(),
        "btc_price_usdt": str(data.btc_price_usdt),
        "network_difficulty": str(data.network_difficulty),
        "block_reward_btc": str(data.block_reward_btc),
    }
