"""Shared in-memory JWT store for the bot.

Maps Telegram user id -> access token. Tokens live only in process memory
and are never persisted to disk. Kept in its own module so command handlers
and inline-menu handlers share a single source of truth.
"""
from __future__ import annotations

token_store: dict[int, str] = {}
