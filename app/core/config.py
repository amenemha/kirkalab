from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "kirkalab"
    environment: str = "development"
    debug: bool = False
    database_url: str = "sqlite:///./kirkalab.db"
    secret_key: str = "CHANGE_ME"
    access_token_expire_minutes: int = 60
    algorithm: str = "HS256"
    host: str = "127.0.0.1"
    port: int = 8000

    # First-admin bootstrap (optional). If all three are set and no user with
    # this email exists, an admin user is created on startup.
    first_admin_email: str | None = None
    first_admin_handle: str | None = None
    first_admin_password: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    if settings.environment.lower() == "production" and settings.secret_key == "CHANGE_ME":
        raise ValueError("SECRET_KEY must be set in production")

    return settings
