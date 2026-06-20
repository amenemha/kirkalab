"""Admin-alert plumbing for the bot (Queue 2.4 monitoring).

This module is deliberately **aiogram-independent** so the throttle and the
secret-masking logic can be unit-tested in the backend CI (which runs without
aiogram). The thin aiogram wiring — a global errors handler and the actual
``bot.send_message`` call — lives in :mod:`bot.notifier`.

Two concerns live here:

* :func:`mask_secrets` — scrubs tokens, the X-Bot-Secret, passwords and user
  emails out of any text before it is logged or sent to the admin chat.
* :class:`AlertThrottle` — per-key time-window dedup so a flapping backend can
  not spam the admin chat (e.g. at most one alert per error type per N minutes).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Patterns are intentionally broad: it is better to over-mask than to leak a
# secret into a log line or an admin message. Each captures a label so the
# replacement keeps the text readable ("token=***").
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Credentials embedded in connection URLs (redis://user:pass@host,
    # postgresql://user:pass@host). Redact the userinfo, keep scheme + host.
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"),
        r"\1***:***@",
    ),
    # bot:NNN:AAA... Telegram tokens.
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "***TELEGRAM_TOKEN***"),
    # Bearer tokens — matched before the generic key=value rule so the whole
    # token is redacted, not just the "Authorization" label.
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+"), "Bearer ***"),
    # key=value style secrets (token=..., password=..., secret=..., x-bot-secret: ...).
    (
        re.compile(
            r"(?i)\b(token|password|passwd|secret|x[-_]bot[-_]secret|authorization|api[-_]?key)\b"
            r"\s*[:=]\s*['\"]?[^\s'\"&]+",
        ),
        r"\1=***",
    ),
    # Email addresses (user PII).
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "***EMAIL***",
    ),
)


def safe_endpoint(url: str) -> str:
    """Return ``host:port`` from a connection URL, dropping any credentials.

    Uses ``urllib.parse.urlsplit`` so the userinfo (``user:pass@``) is removed at
    the parser level — the result is not derived from the secret part of the URL
    and is safe to log. This is the *correct* way to log a connection target:
    regex masking is not recognised as a sanitizer by static analysis, but
    ``urlsplit().hostname/.port`` provably never contain the credentials.
    """
    parts = urlsplit(url)
    return f"{parts.hostname or '?'}:{parts.port or '?'}"


def mask_secrets(text: str) -> str:
    """Return ``text`` with tokens, secrets, passwords and emails redacted.

    Safe to call on arbitrary strings (tracebacks, exception messages, URLs)
    before logging or sending them to the admin chat.
    """
    if not text:
        return text
    masked = text
    for pattern, replacement in _SECRET_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


@dataclass
class AlertThrottle:
    """Time-window dedup for outbound alerts, keyed by an arbitrary string.

    :func:`allow` returns ``True`` at most once per ``interval_seconds`` for a
    given key. The default key is the alert "type" (e.g. ``"backend_5xx"``) so a
    storm of identical failures collapses to one message per window.

    Pure and clock-injectable (``time_func``) so it is trivially unit-testable
    without sleeping. Not thread-safe; the bot runs single-threaded asyncio.
    """

    interval_seconds: float = 300.0
    _last_sent: dict[str, float] = field(default_factory=dict)
    time_func: "callable" = field(default=time.monotonic)

    def allow(self, key: str) -> bool:
        """Return ``True`` if an alert for ``key`` may be sent now."""
        now = self.time_func()
        last = self._last_sent.get(key)
        if last is not None and (now - last) < self.interval_seconds:
            return False
        self._last_sent[key] = now
        return True

    def reset(self, key: str | None = None) -> None:
        """Forget the last-sent time for ``key`` (or all keys when ``None``)."""
        if key is None:
            self._last_sent.clear()
        else:
            self._last_sent.pop(key, None)
