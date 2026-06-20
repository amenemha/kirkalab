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
  # Public bot username (without @), used for deep links if needed.
  bot_username: str = "roibot_ai_bot"
  # Shared secret sent as X-Bot-Secret when approving QR sessions. Must match
  # the API's BOT_INTERNAL_SECRET. Without it QR approval cannot work.
  bot_internal_secret: str | None = None
  # Base URL of the Kirkalab API (e.g. http://app:8000 inside docker network).
  api_base_url: str = "http://app:8000"
  # Request timeout for API calls, seconds.
  request_timeout: float = 10.0
  # Redis connection for FSM persistence. Defaults to the compose `redis`
  # service so a container restart no longer drops in-flight user state.
  redis_url: str = "redis://redis:6379/0"
  # Telegram chat id that receives operational alerts. Optional: when unset,
  # alerting is disabled and the bot still runs normally.
  admin_chat_id: int | None = None
  # Minimum interval (seconds) between repeated alerts of the same type, so a
  # flapping backend can not spam the admin chat.
  alert_throttle_seconds: float = 300.0


@lru_cache
def get_settings() -> BotSettings:
  return BotSettings()
