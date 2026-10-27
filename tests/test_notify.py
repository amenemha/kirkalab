"""Уведомления фермы: настройки, сон, сгруппированный дайджест без «мигания»."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import farm_models as fm
from app.services.notify import service as ns

TG = 183868623
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone(timedelta(hours=3))).timestamp()


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "owner_telegram_ids", [TG])


def _devs(db, n_off=3, n_err=2, since_off=3600):
    c = fm.FarmContainer(ext_id="c1", name="Контейнер А")
    db.add(c)
    db.flush()
    t = datetime.fromtimestamp(NOW - since_off, timezone.utc)
    out = []
    for i in range(n_off):
        out.append(fm.FarmDevice(ext_id=f"o{i}", model="S21", container_id=c.id, status="offline", status_since=t, worker=f"w.o{i}"))
    for i in range(n_err):
        out.append(fm.FarmDevice(ext_id=f"e{i}", model="S21", container_id=c.id, status="working", worker=f"w.e{i}",
                                 alarms=[{"code": "540", "msg": "chip id error", "cls": "hardware", "level": 2, "since": NOW - 3600}]))
    db.add_all(out)
    db.commit()
    return out


def test_prefs_defaults_and_validation(db):
    p = ns.get_prefs(db, TG)
    assert p["enabled"] and p["types"]["offline"] and p["sleep"]["from"] == "23:00"
    p = ns.set_prefs(db, TG, {"types": {"pool": True}, "sleep": {"from": "22:30"}})
    assert p["types"]["pool"] is True and p["types"]["offline"] is True and p["sleep"]["from"] == "22:30"
    with pytest.raises(ValueError):
        ns.set_prefs(db, TG, {"every_min": 1})
    with pytest.raises(ValueError):
        ns.set_prefs(db, TG, {"daily_at": "25:00"})


def test_sleep_window_crosses_midnight():
    p = dict(ns.DEFAULTS)
    msk = timezone(timedelta(hours=3))
    assert ns.in_sleep(p, datetime(2026, 1, 1, 23, 30, tzinfo=msk))
    assert ns.in_sleep(p, datetime(2026, 1, 1, 7, 59, tzinfo=msk))
    assert not ns.in_sleep(p, datetime(2026, 1, 1, 8, 0, tzinfo=msk))


def test_first_tick_is_silent_baseline_then_reports_changes(db):
    _devs(db)
    sent = []
    sender = lambda tg, text: sent.append((tg, text)) or True  # noqa: E731
    ns.set_prefs(db, TG, {"types": {"daily": False}})
    assert ns.tick(db, NOW, sender) == 0  # базовая линия, без спама 1500 старыми офлайнами
    # Новый офлайн дольше hold_min + один вернулся.
    d = fm.FarmDevice(ext_id="n1", model="S19", status="offline", status_since=datetime.fromtimestamp(NOW - 1200, timezone.utc))
    db.add(d)
    back = db.query(fm.FarmDevice).filter_by(ext_id="o0").one()
    back.status = "working"
    db.commit()
    assert ns.tick(db, NOW + 16 * 60, sender) == 1
    text = sent[-1][1]
    assert "Нет связи: +1" in text and "Вернулись в работу: 1" in text
    # Повторно о том же не пишем.
    assert ns.tick(db, NOW + 32 * 60, sender) == 0


def test_flapping_below_hold_is_ignored(db):
    ns.set_prefs(db, TG, {"types": {"daily": False}, "hold_min": 10})
    ns.tick(db, NOW, lambda *a: True)
    db.add(fm.FarmDevice(ext_id="f1", model="S19", status="offline", status_since=datetime.fromtimestamp(NOW + 15 * 60, timezone.utc)))
    db.commit()
    sent = []
    ns.tick(db, NOW + 16 * 60, lambda tg, t: sent.append(t) or True)
    assert sent == []


def test_sleep_holds_messages_unless_mass_outage(db):
    ns.set_prefs(db, TG, {"types": {"daily": False}, "wake_offline": 5})
    night = datetime(2026, 10, 27, 2, 0, tzinfo=timezone(timedelta(hours=3))).timestamp()
    ns.tick(db, night, lambda *a: True)
    t = datetime.fromtimestamp(night - 3600, timezone.utc)
    db.add_all([fm.FarmDevice(ext_id=f"s{i}", model="S19", status="offline", status_since=t) for i in range(2)])
    db.commit()
    sent = []
    ns.tick(db, night + 20 * 60, lambda tg, x: sent.append(x) or True)
    assert sent == []  # во сне — тихо
    db.add_all([fm.FarmDevice(ext_id=f"m{i}", model="S19", status="offline", status_since=t) for i in range(6)])
    db.commit()
    ns.tick(db, night + 40 * 60, lambda tg, x: sent.append(x) or True)
    assert sent and "во время сна" in sent[0] and "Нет связи: +8" in sent[0]


def test_bot_notify_endpoints(client):
    h = {"X-Bot-Secret": "test-bot-secret"}
    r = client.get("/api/v1/internal/notify", params={"telegram_user_id": TG}, headers=h)
    assert r.status_code == 200 and "offline" in r.json()["types"]
    r = client.put("/api/v1/internal/notify", json={"telegram_user_id": TG, "values": {"min_level": 3}}, headers=h)
    assert r.status_code == 200 and r.json()["prefs"]["min_level"] == 3
    r = client.get("/api/v1/internal/notify", params={"telegram_user_id": 1}, headers=h)
    assert r.status_code == 403


def test_bot_notify_render():
    pytest.importorskip("aiogram")
    from bot.handlers.notify import render

    text, kb = render({"prefs": ns.get_prefs.__globals__["_merge"](ns.DEFAULTS, {}), "types": ns.TYPES})
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "notif:t:offline" in cbs and "notif:test" in cbs and "notif:sleept" in cbs
    assert "Сон: 23:00–08:00" in text and "пауз" not in text.lower()


def _container(db, name, n, ext):
    c = fm.FarmContainer(ext_id=ext, name=name)
    db.add(c)
    db.flush()
    devs = [fm.FarmDevice(ext_id=f"{ext}-{i}", model="S21", container_id=c.id, status="working", worker=f"{ext}.{i}") for i in range(n)]
    db.add_all(devs)
    db.commit()
    return c, devs


def _down(db, devs, at_ts):
    for d in devs:
        d.status, d.status_since = "offline", datetime.fromtimestamp(at_ts, timezone.utc)
    db.commit()


def test_grid_from_name():
    assert ns.grid_of("11к 21 Ф24 (КТП 7) hydro Bitmain") == ("Ф24", ["7"])
    assert ns.grid_of("Б 102-103 ↓ Ф1 (КТП 1 КТП 4) С19") == ("Ф1", ["1", "4"])
    assert ns.grid_of("11К HD5 40-41  Ф1 (КТП 2,3,5) IBMM") == ("Ф1", ["2", "3", "5"])
    assert ns.grid_of("Контейнер") == (None, [])


def test_power_whole_ktp_immediate_then_back(db):
    c1, d1 = _container(db, "11к 21 Ф24 (КТП 7) hydro", 10, "k11")
    c2, d2 = _container(db, "12к 22 Ф24 (КТП 7) hydro", 8, "k12")
    _container(db, "13к 23 Ф24 (КТП 6) hydro", 6, "k13")
    ns.set_prefs(db, TG, {"types": {"daily": False}})
    sent = []
    snd = lambda tg, t: sent.append(t) or True  # noqa: E731
    assert ns.tick(db, NOW, snd) == 0  # базовая линия
    _down(db, d1 + d2, NOW + 60)
    # через 4 минуты — сразу, не дожидаясь сводки (every_min 15)
    assert ns.tick(db, NOW + 5 * 60, snd) == 1
    t = sent[-1]
    assert "вероятно, питание" in t and "11к (Ф24, КТП 7): пропали разом 10 из 10" in t and "12к" in t
    assert "весь КТП 7" in t and "фидер" not in t
    # сводка через 16 минут не повторяет эти 18 асиков списком «Нет связи»
    n = len(sent)
    ns.tick(db, NOW + 17 * 60, snd)
    assert all("Нет связи" not in x for x in sent[n:])
    # вернулись
    for d in d1 + d2:
        d.status = "working"
    db.commit()
    assert ns.tick(db, NOW + 20 * 60, snd) == 1
    assert "11к: вернулось, в работе 10 из 10" in sent[-1]
    n = len(sent)
    ns.tick(db, NOW + 40 * 60, snd)
    assert all("Вернулись в работу" not in x for x in sent[n:])


def test_power_whole_feeder_and_gradual_ignored(db):
    _, d1 = _container(db, "11к Ф24 (КТП 7)", 6, "a")
    _, d2 = _container(db, "13к Ф24 (КТП 6)", 6, "b")
    _, d3 = _container(db, "2к Ф1 (КТП 1)", 6, "c")
    ns.set_prefs(db, TG, {"types": {"daily": False}})
    sent = []
    snd = lambda tg, t: sent.append(t) or True  # noqa: E731
    ns.tick(db, NOW, snd)
    _down(db, d1 + d2, NOW + 60)
    # 2к теряет асики постепенно, по одному раз в 10 минут — это не «питание»
    for i, d in enumerate(d3):
        d.status, d.status_since = "offline", datetime.fromtimestamp(NOW - 3600 + i * 600, timezone.utc)
    db.commit()
    ns.tick(db, NOW + 5 * 60, snd)
    t = sent[-1]
    assert "весь фидер Ф24" in t and "КТП 7 —" not in t and "2к" not in t


def test_power_sleep_wakes_only_if_allowed(db):
    _, d1 = _container(db, "11к Ф24 (КТП 7)", 6, "s")
    night = datetime(2026, 10, 27, 2, 0, tzinfo=timezone(timedelta(hours=3))).timestamp()
    ns.set_prefs(db, TG, {"types": {"daily": False}, "wake_offline": 0})
    sent = []
    snd = lambda tg, t: sent.append(t) or True  # noqa: E731
    ns.tick(db, night, snd)
    _down(db, d1, night + 60)
    ns.tick(db, night + 5 * 60, snd)
    assert not any("питание" in x for x in sent)
    ns.set_prefs(db, TG, {"wake_offline": 30})
    ns.tick(db, night + 6 * 60, snd)
    assert "питание" in sent[-1] and "во время сна" in sent[-1]
