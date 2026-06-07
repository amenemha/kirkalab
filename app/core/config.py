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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
