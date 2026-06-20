"""Pure (no-aiogram) tests for the bot's client-side email validation.

These give the user a warm Russian hint before the API is even called, so they
must run in the backend CI where aiogram is absent."""
from bot.validation import EMAIL_HINT, looks_like_email


def test_accepts_plausible_emails():
    assert looks_like_email("user@mail.ru")
    assert looks_like_email("  user.name+tag@sub.example.com  ")


def test_rejects_obvious_non_emails():
    assert not looks_like_email("not-an-email")
    assert not looks_like_email("a@b")
    assert not looks_like_email("@mail.ru")
    assert not looks_like_email("user@@mail.ru")


def test_rejects_empty_or_none():
    assert not looks_like_email("")
    assert not looks_like_email(None)


def test_hint_is_russian_and_actionable():
    assert "email" in EMAIL_HINT.lower()
    assert "@" in EMAIL_HINT
