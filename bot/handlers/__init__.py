"""Handler routers for the Kirkalab bot.

``routers`` lists the routers in include order. The QR deep-link router is
registered before the menu router so a ``/start qr_<id>`` payload is handled
as a login request rather than the plain greeting.
"""
from __future__ import annotations

from aiogram import Router

from bot.handlers import account, menu, qr

routers: tuple[Router, ...] = (qr.router, menu.router, account.router)

__all__ = ["routers"]
