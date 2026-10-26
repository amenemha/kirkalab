"""Bot hub: menu layout, farm/problems cards, no forbidden words anywhere in bot texts."""
import pathlib
import re

import pytest

pytest.importorskip("aiogram")

from bot.handlers import hub  # noqa: E402

SAMPLE = {"total": 5491, "working": 4157, "offline": 1334, "suspended": 0, "with_alarms": 1426, "ths": 845200, "kw": 18080,
          "efficiency": 101.0, "synced_at": "2026-10-23T19:30:00+00:00",
          "incidents": [{"scope": "11к 21", "down": 191, "total": 191, "ended": None}],
          "classes": [{"label": "Питание", "count": 91}, {"label": "Нет связи", "count": 1333}],
          "containers": [{"name": "4к 6-7 ↓ Ф1 (КТП 2) P33 IBMM", "offline": 352, "total": 407},
                         {"name": "5а 8-9", "offline": 0, "total": 199}]}


def test_hub_menu_two_per_row_and_site_button():
    kb = hub.hub_kb("https://kirkalab.ru/#s=x")
    rows = kb.inline_keyboard
    assert all(len(r) == 2 for r in rows[:-1])
    last = rows[-1][0]
    assert last.url.startswith("https://kirkalab.ru/") and last.style == "success"
    labels = " ".join(b.text for r in rows for b in r)
    for w in ("Ферма", "Проблемы", "Пулы", "Расчёт", "Открыть Kirka"):
        assert w in labels


def test_problems_text_sorted_and_named():
    t = hub.problems_text(SAMPLE)
    assert "1 334" in t and "Аварии" in t
    assert t.index("Нет связи") < t.index("Питание")  # biggest first
    assert "5а 8-9" not in t  # containers without offline are not listed


def test_problems_text_calm():
    t = hub.problems_text({"total": 10, "offline": 0, "with_alarms": 0})
    assert "Всё спокойно" in t


def test_no_forbidden_words_in_bot_texts():
    bad = re.compile(r"купит|купи|оплат|тариф|цена|цену|скидк", re.I)
    hits = []
    for p in pathlib.Path("bot").rglob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or '"' not in s:
                continue
            for lit in re.findall(r'"([^"]*)"', s):
                if bad.search(lit) and not lit.startswith(("profile:", "billing:")):
                    hits.append(f"{p}:{i}: {lit}")
    assert not hits, "\n".join(hits)


def test_farm_text_blocks_and_sleep_word():
    t = hub.farm_text({"total": 100, "working": 80, "offline": 15, "suspended": 5, "with_alarms": 7, "ths": 9000, "kw": 300,
                       "sites": [{"name": "A", "total": 60, "working": 50}, {"name": "B", "total": 40, "working": 30}],
                       "incidents": [{"scope": "1а 0-1", "down": 10, "total": 10, "ended": None}]})
    assert "<blockquote>" in t and "1️⃣" in t and "Сон — 5" in t and "Площадки" in t and "Аварии" in t
    assert "пауз" not in t.lower()
    assert t.count("<blockquote") == t.count("</blockquote>")
