"""Entrypoint for the Kirkalab Telegram bot.

Starts long-polling against the Telegram Bot API and wires the API client
onto the Bot instance so handlers can reach it. Run with:

    python -m bot.main

FSM state is persisted in Redis (``REDIS_URL``) so a container restart no
longer drops in-flight user flows (calc, PRO email/password binding). Critical
events (unhandled handler exceptions, backend failures) are forwarded to the
admin chat when ``ADMIN_CHAT_ID`` is configured.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.alerts import safe_endpoint
from bot.api_client import KirkalabApiClient
from bot.config import get_settings
from bot.handlers import routers
from bot.notifier import AdminNotifier, register_error_alerts

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("kirkalab.bot")


def build_storage(redis_url: str):
  """Return a Redis-backed FSM storage, or fail loudly if Redis is unreachable.

  RedisStorage and its redis client are imported lazily so the import graph
  does not require the redis package in environments that never start the bot
  (e.g. the backend test suite).
  """
  from aiogram.fsm.storage.redis import RedisStorage

  # Only the host:port is ever logged — never the URL itself, which may carry
  # credentials (redis://user:pass@host). safe_endpoint() strips any userinfo at
  # the parser level, so no secret-derived value reaches the logger.
  endpoint = safe_endpoint(redis_url)
  try:
    storage = RedisStorage.from_url(redis_url)
  except Exception as exc:  # noqa: BLE001 — surface a clear, actionable error
    # Log only host:port and the exception class — never the URL or the
    # exception message/traceback (either can embed credentials).
    logger.error(
      "Could not initialise Redis FSM storage at %s: %s", endpoint, type(exc).__name__
    )
    raise
  logger.info("FSM storage: Redis (%s)", endpoint)
  return storage


async def main() -> None:
  settings = get_settings()

  bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
  )

  notifier = AdminNotifier(
    bot,
    admin_chat_id=settings.admin_chat_id,
    throttle_interval_seconds=settings.alert_throttle_seconds,
  )
  # Share a single API client + notifier with all handlers via the bot instance.
  bot.kirkalab_client = KirkalabApiClient(
    base_url=settings.api_base_url,
    timeout=settings.request_timeout,
    notifier=notifier,
  )
  bot.kirkalab_notifier = notifier

  try:
    storage = build_storage(settings.redis_url)
  except Exception:
    # Persistence is required in production; do not silently fall back to an
    # in-memory store that would lose state on the next restart.
    logger.critical("Bot startup aborted: Redis FSM storage is unavailable")
    raise

  dispatcher = Dispatcher(storage=storage)
  for handler_router in routers:
    dispatcher.include_router(handler_router)
  register_error_alerts(dispatcher, notifier)

  logger.info("Starting Kirkalab bot (API: %s)", settings.api_base_url)
  if notifier.enabled:
    logger.info("Admin alerts enabled")
  else:
    logger.info("Admin alerts disabled (ADMIN_CHAT_ID not set)")
  try:
    await dispatcher.start_polling(bot)
  finally:
    await bot.session.close()
    await storage.close()


# Re-exported for environments/tests that need an in-memory fallback explicitly.
__all__ = ["main", "build_storage", "MemoryStorage"]


if __name__ == "__main__":
  asyncio.run(main())
