"""Tests for the aiogram-independent alert plumbing (Queue 2.4 monitoring).

``bot.alerts`` imports nothing from aiogram, so these run in the backend CI
without ``pytest.importorskip``.
"""
from __future__ import annotations

from bot.alerts import AlertThrottle, mask_secrets, safe_endpoint


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_throttle_allows_first_then_blocks_within_window():
    clock = _FakeClock()
    throttle = AlertThrottle(interval_seconds=300, time_func=clock)

    assert throttle.allow("backend_5xx") is True
    # Same key inside the window is suppressed.
    assert throttle.allow("backend_5xx") is False
    clock.advance(299)
    assert throttle.allow("backend_5xx") is False


def test_throttle_allows_again_after_window():
    clock = _FakeClock()
    throttle = AlertThrottle(interval_seconds=300, time_func=clock)

    assert throttle.allow("timeout") is True
    clock.advance(300)
    assert throttle.allow("timeout") is True


def test_throttle_keys_are_independent():
    clock = _FakeClock()
    throttle = AlertThrottle(interval_seconds=300, time_func=clock)

    assert throttle.allow("type_a") is True
    # A different key is not affected by type_a's window.
    assert throttle.allow("type_b") is True


def test_throttle_reset_clears_window():
    clock = _FakeClock()
    throttle = AlertThrottle(interval_seconds=300, time_func=clock)

    assert throttle.allow("k") is True
    throttle.reset("k")
    assert throttle.allow("k") is True


def test_mask_telegram_token():
    text = "token is 123456789:AAEhBOweik6ad-vmX1example_tokenXYZ123 end"
    masked = mask_secrets(text)
    assert "AAEhBOweik6ad" not in masked
    assert "***TELEGRAM_TOKEN***" in masked


def test_mask_key_value_secrets():
    text = "X-Bot-Secret: supersecretvalue and password=hunter2 plus token=abc123def"
    masked = mask_secrets(text)
    assert "supersecretvalue" not in masked
    assert "hunter2" not in masked
    assert "abc123def" not in masked


def test_mask_bearer_token():
    masked = mask_secrets("Authorization: Bearer eyJhbGciOi.payload.sig")
    # The token value must be gone; the exact redaction marker is unimportant.
    assert "eyJhbGciOi.payload.sig" not in masked
    assert "***" in masked


def test_mask_standalone_bearer_token():
    masked = mask_secrets("got header Bearer eyJhbGciOi.payload.sig here")
    assert "eyJhbGciOi.payload.sig" not in masked
    assert "Bearer ***" in masked


def test_mask_email():
    masked = mask_secrets("user alice@example.com hit an error")
    assert "alice@example.com" not in masked
    assert "***EMAIL***" in masked


def test_mask_redis_url_credentials():
    masked = mask_secrets("connecting to redis://admin:s3cr3tpw@redis:6379/0")
    assert "s3cr3tpw" not in masked
    assert "admin" not in masked
    # Scheme and host are kept for diagnostics.
    assert "redis://" in masked
    assert "redis:6379" in masked


def test_mask_database_url_credentials():
    url = "postgresql+psycopg://kirka:dbpass123@db:5432/kirkalab"
    masked = mask_secrets(f"could not connect: {url}")
    assert "dbpass123" not in masked
    assert "kirka:" not in masked


def test_mask_url_without_credentials_is_untouched():
    masked = mask_secrets("API: http://app:8000")
    assert masked == "API: http://app:8000"


def test_safe_endpoint_drops_credentials():
    # This is the value logged for Redis startup — it must never carry user:pass.
    endpoint = safe_endpoint("redis://admin:s3cr3tpw@redis:6379/0")
    assert endpoint == "redis:6379"
    assert "s3cr3tpw" not in endpoint
    assert "admin" not in endpoint


def test_safe_endpoint_without_credentials():
    assert safe_endpoint("redis://redis:6379/0") == "redis:6379"


def test_safe_endpoint_missing_parts():
    # Degrades gracefully when host/port are absent rather than raising.
    assert safe_endpoint("redis://") == "?:?"


def test_mask_is_safe_on_empty_and_plain_text():
    assert mask_secrets("") == ""
    assert mask_secrets("nothing sensitive here") == "nothing sensitive here"
