from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "kirkalab"
    environment: str = "development"
    debug: bool = False
    database_url: str = "sqlite:///./kirkalab.db"
    secret_key: str = "CHANGE_ME"
    # Allowed CORS origins, parsed from the comma-separated CORS_ORIGINS env var.
    cors_origins: list[str] = []
    access_token_expire_minutes: int = 60
    refresh_token_expire_minutes: int = 60 * 24 * 14
    email_token_expire_minutes: int = 60 * 24
    reset_token_expire_minutes: int = 30
    auth_rate_limit: str = "10/minute"
    algorithm: str = "HS256"
    host: str = "127.0.0.1"
    port: int = 8000

    # First-admin bootstrap (optional). If all three are set and no user with
    # this email exists, an admin user is created on startup.
    first_admin_email: str | None = None
    first_admin_handle: str | None = None
    first_admin_password: str | None = None

    # Telegram QR-login.
    bot_username: str = "roibot_ai_bot"
    # Shared secret the bot must present (X-Bot-Secret) to approve QR sessions.
    bot_internal_secret: str | None = None
    qr_session_ttl_seconds: int = 120

    # Market data integrations (CoinGecko + mempool.space, both public).
    coingecko_base_url: str = "https://api.coingecko.com"
    mempool_base_url: str = "https://mempool.space"
    # How long an in-memory market snapshot is reused before refreshing.
    market_cache_ttl_seconds: int = 300
    # How old a persisted snapshot may be and still serve as a fallback.
    market_max_staleness_seconds: int = 3600
    # Per-request HTTP timeout and number of extra attempts on failure.
    market_http_timeout_seconds: float = 5.0
    market_http_retries: int = 2
    market_default_block_reward_btc: str = "3.125"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    if settings.environment.lower() == "production" and settings.secret_key == "CHANGE_ME":
        raise ValueError("SECRET_KEY must be set in production")

    if settings.environment.lower() == "production" and settings.debug:
        raise ValueError("DEBUG must be disabled in production")

    if settings.environment.lower() == "production" and not settings.bot_internal_secret:
        raise ValueError("BOT_INTERNAL_SECRET must be set in production")

    return settings
