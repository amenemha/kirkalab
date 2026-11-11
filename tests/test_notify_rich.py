"""Уведомления фермы таблицами (rich) — те же данные, что в тексте."""
from app.services import tg
from app.services.notify import service as ns

DIGEST = """🔔 <b>Ферма</b> · 18:27   🔴 +2  ⚠️ 1  🔄 1

🔴 <b>Нет связи: +2</b> <i>· 6к 10-11: 2</i>
<blockquote expandable><code>10.116.10.41</code> · T21 · 6к 10-11
<code>10.116.11.16</code> · T21 · 6к 10-11</blockquote>
⚠️ <b>Новые ошибки: 1</b> — Питание 1 <i>· Б 102-103: 1</i>
<blockquote expandable><code>10.116.103.19</code> · M31S_VE10 · Б 102-103 — Power current error</blockquote>
🔄 <b>Сменили пул или воркер: 1</b>
   • аккаунт EktonT21 → Ecton · на пул btc.viabtc.io:3333 — <b>1</b>
<blockquote expandable><code>10.116.6.10</code> · T21 · 4к 6-7 · был btc.ru.viabtc.me:3333
   <code>EktonT21.T21x190x2014892</code> → <code>Ecton.116x6x10</code>
… и ещё 5 — кнопка «Открыть эти асики»</blockquote>
<i>ⓘ Комиссия прошивки (DevFee) сейчас у 2 — это норма, не тревога.</i>"""


def test_digest_to_rich_tables():
    r = ns.to_rich(DIGEST)
    assert r.startswith("<h3>🔔 Ферма · 18:27</h3>")
    assert "<th>Что</th><th>Сколько</th>" in r
    assert "<td>🔴 Нет связи</td><td align=\"center\"><b>+2</b></td><td>6к 10-11: 2</td>" in r
    assert r.count("<details>") == 3 and "blockquote" not in r
    for ip in ("10.116.10.41", "10.116.11.16", "10.116.103.19", "10.116.6.10"):
        assert f"<code>{ip}</code>" in r
    assert "<th>Что</th></tr>" in r and "Power current error" in r
    assert "<code>EktonT21.T21x190x2014892</code> → <code>Ecton.116x6x10</code><br>прежний пул: btc.ru.viabtc.me:3333" in r
    assert "аккаунт EktonT21 → Ecton · на пул btc.viabtc.io:3333</td><td align=\"right\"><b>1</b>" in r
    assert "и ещё 5" in r and "<footer>ⓘ Комиссия прошивки" in r


def test_daily_and_power_to_rich():
    d = ns.to_rich("☀️ <b>Kirka · сводка на 11.11 08:00</b>\n\n🟢 В работе: 205 из 4425\n🔴 Нет связи: 2542\n"
                   "⚡ Хешрейт: 16.44 PH/s · 687 кВт\nБольше всего без связи: 2к 30")
    assert "<td>🟢 В работе</td><td align=\"right\"><b>205 из 4425</b></td>" in d and "<footer>Больше всего" in d
    p = ns.to_rich("⚡ <b>Kirka · вероятно, питание</b> · 17:40\n\n🔴 2к (Ф1, КТП 1): пропали разом 9 из 30 в 17:40\n\n"
                   "Если питание есть — проверьте связь контейнера: коммутатор, провайдер.")
    assert "<td>🔴 2к (Ф1, КТП 1)</td>" in p and "<footer>Если питание есть" in p


def test_send_uses_rich_then_falls_back(monkeypatch):
    calls = []

    def fake(method, payload, timeout=15):
        calls.append((method, payload))
        return {"ok": method == "sendMessage"}

    monkeypatch.setattr(tg, "call", fake)
    monkeypatch.setattr(ns, "get_settings", lambda: type("S", (), {"bot_token": "x"})())
    assert ns.send_telegram(1, DIGEST, link="https://kirkalab.ru/#ev/abc")
    assert [m for m, _ in calls] == ["sendRichMessage", "sendMessage"]
    rk = calls[0][1]["reply_markup"]["inline_keyboard"][0]
    assert rk[0]["url"].endswith("#ev/abc") and rk[1]["callback_data"] == "notif:open"
    assert calls[1][1]["text"] == DIGEST
