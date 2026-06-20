"""Pure input validation for the bot (no aiogram dependency).

Kept separate from the handlers so it can be unit-tested in the backend test
environment (no Telegram libraries needed). The point is to catch obvious bad
input *before* calling the API, so the user sees a warm Russian hint instead of
a raw English pydantic error like ``value is not a valid email address``.
"""
from __future__ import annotations

import re

# Deliberately permissive: one ``@``, a non-empty local part, a dotted domain.
# The API still does the authoritative EmailStr validation; this only filters
# the obviously-wrong input to give a friendly message first.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

EMAIL_HINT = "Похоже, это не email — пришлите адрес вида name@mail.ru"


def looks_like_email(text: str | None) -> bool:
    """True when ``text`` is plausibly an email address."""
    if not text:
        return False
    return bool(_EMAIL_RE.match(text.strip()))
