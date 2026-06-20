"""Reply-keyboard main menu (CALC_SPEC §3.2): exactly 4 buttons, no tariff/help.

The menu *data* is pure (no aiogram) and tested directly; the built
``ReplyKeyboardMarkup`` is checked only when aiogram is installed."""
from bot.menu_items import REPLY_MENU_BY_TEXT, REPLY_MENU_ITEMS


def test_reply_menu_has_exactly_four_buttons():
    assert len(REPLY_MENU_ITEMS) == 4


def test_reply_menu_actions_and_texts():
    actions = [a for a, _ in REPLY_MENU_ITEMS]
    assert actions == ["calculator", "catalog", "reports", "profile"]


def test_reply_menu_excludes_tariff_and_help():
    # Tariff/PRO and Help live inside Profile, never as top-level buttons.
    texts = " ".join(t for _, t in REPLY_MENU_ITEMS).lower()
    assert "тариф" not in texts
    assert "помощь" not in texts
    assert "pro" not in texts


def test_reply_menu_text_lookup_is_complete():
    for action, text in REPLY_MENU_ITEMS:
        assert REPLY_MENU_BY_TEXT[text] == action


def test_built_reply_markup_is_persistent_2x2():
    import pytest

    pytest.importorskip("aiogram")
    from bot.keyboards import main_reply_menu

    markup = main_reply_menu()
    assert markup.is_persistent is True
    assert markup.resize_keyboard is True
    # 2x2 layout -> two rows of two buttons each.
    assert len(markup.keyboard) == 2
    assert all(len(row) == 2 for row in markup.keyboard)
