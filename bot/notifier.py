"""Aiogram wiring for admin alerts (Queue 2.4 monitoring).

Depends on aiogram; the reusable, testable logic (throttling + secret masking)
lives in :mod:`bot.alerts`. This module exposes:

* :class:`AdminNotifier` — sends a (masked, throttled) message to the admin chat
  when one is configured. A no-op when ``ADMIN_CHAT_ID`` is unset, so deployments
  without an admin chat simply do not get alerts (never crash).
* :func:`register_error_alerts` — installs a global aiogram errors handler that
  logs every unhandled handler exception and alerts the admin with a short,
  secret-free traceback.
"""
from __future__ import annotations

import logging
import traceback

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from bot.alerts import AlertThrottle, mask_secrets

logger = logging.getLogger("kirkalab.bot.alerts")

# Cap traceback length so an alert never hits Telegram's 4096-char message limit.
_MAX_TRACEBACK_CHARS = 1500


class AdminNotifier:
    """Sends throttled, secret-masked alerts to the admin chat.

    When ``admin_chat_id`` is ``None`` every method is a no-op: alerting is an
    optional feature and its absence must never break the bot.
    """

    def __init__(
        self,
        bot: Bot,
        admin_chat_id: int | None,
        throttle_interval_seconds: float = 300.0,
    ) -> None:
        self._bot = bot
        self._admin_chat_id = admin_chat_id
        self._throttle = AlertThrottle(interval_seconds=throttle_interval_seconds)

    @property
    def enabled(self) -> bool:
        return self._admin_chat_id is not None

    async def alert(self, text: str, *, key: str | None = None) -> bool:
        """Send ``text`` to the admin chat, masked and (optionally) throttled.

        ``key`` groups alerts for dedup: when given, at most one alert per key
        per throttle window is delivered. Returns ``True`` if a message was
        actually sent. Never raises — a failure to alert is logged, not fatal.
        """
        if not self.enabled:
            return False
        if key is not None and not self._throttle.allow(key):
            logger.debug("Alert throttled (key=%s)", key)
            return False
        safe_text = mask_secrets(text)
        try:
            await self._bot.send_message(self._admin_chat_id, safe_text)
            return True
        except Exception as exc:  # noqa: BLE001 — alerting must never break the bot
            # Log only the failure's class, never exc_info: a send-failure
            # traceback can embed the (sensitive) message payload being sent.
            logger.warning("Failed to deliver admin alert: %s", type(exc).__name__)
            return False

    async def alert_backend_error(self, error_type: str, detail: str) -> bool:
        """Alert about a backend/internal-API failure, throttled per type."""
        text = f"⚠️ Backend error [{error_type}]: {detail}"
        return await self.alert(text, key=f"backend:{error_type}")


def register_error_alerts(dispatcher: Dispatcher, notifier: AdminNotifier) -> None:
    """Install a global errors handler that logs + alerts on any handler crash."""

    @dispatcher.errors()
    async def _on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        # Log only the non-sensitive exception class. The exception message and
        # traceback can carry user-supplied data, tokens or credentialed URLs, so
        # they are never written to the log in clear text. The masked traceback
        # tail still reaches the operator via the admin alert below (which masks
        # again inside AdminNotifier.alert).
        exc_name = type(exc).__name__
        logger.error("Unhandled bot exception: %s", exc_name)
        tb = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        tail = mask_secrets(tb)[-_MAX_TRACEBACK_CHARS:]
        await notifier.alert(
            f"🛑 Необработанное исключение в боте:\n{exc_name}\n\n{tail}",
            key=f"handler_exc:{exc_name}",
        )
        # Returning True marks the error as handled so polling continues.
        return True
