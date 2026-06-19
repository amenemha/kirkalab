"""Entrypoint for the Kirkalab Telegram bot.

Starts long-polling against the Telegram Bot API and wires the API client
onto the Bot instance so handlers can reach it. Run with:

    python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.api_client import KirkalabApiClient
from bot.config import get_settings
from bot.handlers import routers

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("kirkalab.bot")


async def main() -> None:
  settings = get_settings()

  bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
  )
  # Share a single API client with all handlers via the bot instance.
  bot.kirkalab_client = KirkalabApiClient(
    base_url=settings.api_base_url,
    timeout=settings.request_timeout,
  )

  dispatcher = Dispatcher()
  for handler_router in routers:
    dispatcher.include_router(handler_router)

  logger.info("Starting Kirkalab bot (API: %s)", settings.api_base_url)
  try:
    await dispatcher.start_polling(bot)
  finally:
    await bot.session.close()


if __name__ == "__main__":
  asyncio.run(main())
