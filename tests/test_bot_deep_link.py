"""Bot unit tests: QR deep-link parsing and main-menu definition.

These cover the dependency-free bot modules (no aiogram/Telegram needed),
so they run inside the standard backend test environment.
"""
import pytest

from bot.deep_link import QR_PREFIX, parse_qr_payload
from bot.menu_items import MAIN_MENU_ITEMS, STUB_ACTIONS


@pytest.mark.parametrize(
  "payload,expected",
  [
    ("qr_abc123", "abc123"),
    ("qr_" + "x" * 43, "x" * 43),  # token_urlsafe(32) length
    ("  qr_trimmed  ", "trimmed"),
    ("qr_a_b_c", "a_b_c"),  # underscores inside the id are preserved
  ],
)
def test_parse_qr_payload_valid(payload, expected):
  assert parse_qr_payload(payload) == expected


@pytest.mark.parametrize(
  "payload",
  [None, "", "   ", "qr_", "hello", "start=qr_x", "QR_upper"],
)
def test_parse_qr_payload_invalid(payload):
  assert parse_qr_payload(payload) is None


def test_qr_prefix_constant():
  assert QR_PREFIX == "qr_"
  assert parse_qr_payload(f"{QR_PREFIX}session") == "session"


def test_main_menu_has_expected_items():
  actions = [action for action, _ in MAIN_MENU_ITEMS]
  assert actions == ["profile", "calculator", "reports", "plan", "help"]


def test_menu_labels_have_emoji_and_text():
  for action, label in MAIN_MENU_ITEMS:
    assert label.strip(), f"empty label for {action}"
    # Each label is "<emoji> <text>" — at least two whitespace-separated parts.
    assert len(label.split()) >= 2


def test_stub_actions_are_subset_of_menu():
  actions = {action for action, _ in MAIN_MENU_ITEMS}
  assert STUB_ACTIONS <= actions
  # Implemented items must not be stubs.
  assert "profile" not in STUB_ACTIONS
  assert "help" not in STUB_ACTIONS
