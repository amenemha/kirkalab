from bot.handlers.pools import _fmt, _hr


def test_hr_units():
    assert _hr(1600000) == "1,60 EH/s"
    assert _hr(55000) == "55,00 PH/s"
    assert _hr(4.2) == "4,2 TH/s"


def test_fmt_text():
    s = {"updated": "2026-10-23T18:00:00+00:00", "yesterday": "2026-10-22",
         "coins": [{"coin": "BTC", "ths_1h": 1.6e6, "ths_1d": 1.5e6, "w_active": 5000, "w_inactive": 12, "links": 90, "yesterday": 1.55e6}],
         "drops": [{"account": "a1", "group": "g", "coin": "BTC", "ths_1h": 10, "ths_1d": 100}], "drops_total": 1,
         "broken": [{"account": "b1", "group": None, "error": "x"}], "broken_total": 1}
    t = _fmt(s)
    assert "21:00 МСК" in t and "BTC" in t and "Просели" in t and "не читаются" in t
    for w in ("куп", "оплат", "тариф", "цен", "скидк"):
        assert w not in t.lower()
