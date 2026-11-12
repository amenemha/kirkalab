"""Свободный текст: цена после расчёта пересчитывает, короткие реплики не уходят менеджеру."""
from bot.handlers.support import as_price, is_short


def test_as_price():
    assert as_price("5.5") == ("5.5", "RUB")
    assert as_price("5,5 ₽") == ("5.5", "RUB")
    assert as_price("0.06 usdt") == ("0.06", "USDT")
    assert as_price("7 руб") == ("7", "RUB")
    assert as_price("привет") is None
    assert as_price("S21 10 шт") is None


def test_is_short():
    assert is_short("5.5")
    assert is_short("ок")
    assert is_short("привет")
    assert not is_short("почему не видно мою ферму")
    assert not is_short("где?")
    assert not is_short("асики на втором контейнере отвалились")


def test_texts_have_no_internal_ids():
    import pathlib
    src = pathlib.Path("bot/handlers/account.py").read_text() + pathlib.Path("bot/handlers/hub.py").read_text()
    assert "Кабинет: #" not in src
    assert "Тестовый доступ" not in src
    assert "только администраторам" not in src
