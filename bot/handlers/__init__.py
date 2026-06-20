"""Handler routers for the Kirkalab bot.

``routers`` lists the routers in include order. The QR deep-link router is
registered before the menu router so a ``/start qr_<id>`` payload is handled
as a login request rather than the plain greeting. The catalog router is
registered before the menu router too, so its ``menu:catalog`` callback is
handled (opening the catalog) instead of falling through to the menu stub.
"""
from __future__ import annotations

from aiogram import Router

from bot.handlers import account, billing, calc, catalog, history, menu, qr

routers: tuple[Router, ...] = (
  qr.router,
  calc.router,
  catalog.router,
  history.router,
  menu.router,
  billing.router,
  account.router,
)

__all__ = ["routers"]
