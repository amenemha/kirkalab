"""Configuration for the Kirkalab Telegram bot.

All values are read from environment variables (or a local .env file).
No secrets are committed to the repository.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", extra="ignore")

  # Telegram bot token from @BotFather.
  bot_token: str
  # Base URL of the Kirkalab API (e.g. http://app:8000 inside docker network).
  api_base_url: str = "http://app:8000"
  # Request timeout for API calls, seconds.
  request_timeout: float = 10.0


@lru_cache
def get_settings() -> BotSettings:
  return BotSettings()
