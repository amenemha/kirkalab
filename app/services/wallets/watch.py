"""Кошельки людей: добавление, история и уведомления о поступлениях."""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.wallet_models import WalletTx, WalletWatch
from app.services.wallets.chains import CHAINS, ORDER, STABLE, ChainError, Reader, Transfer, detect, norm

log = logging.getLogger("kirka.wallets")
MSK = timezone(timedelta(hours=3))
SITE = "https://kirkalab.ru/"
LIMITS = {"free": 1, "pro": 10, "admin": 100}
CG = {"BTC": "bitcoin", "LTC": "litecoin", "DOGE": "dogecoin", "TRX": "tron", "TON": "the-open-network",
      "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana"}
STATUS = {"ok": "перевод", "dust": "меньше порога", "spam": "спам-токен", "fake": "поддельный токен",
          "poison": "отравление адреса", "zero": "нулевой перевод"}


class WalletError(ValueError):
    """Ошибка с готовым текстом для человека (без деталей исключений)."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


# ---------- курсы ----------
_px: dict = {"at": 0.0, "usd": {}, "rub": None}


def prices(db: Session | None = None) -> tuple[dict, float | None]:
    if time.time() - _px["at"] > 600 or not _px["usd"]:
        try:
            j = httpx.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(CG.values()), "vs_currencies": "usd"},
                          timeout=15).json()
            _px["usd"] = {k: float((j.get(v) or {}).get("usd") or 0) for k, v in CG.items() if (j.get(v) or {}).get("usd")}
            _px["at"] = time.time()
        except (httpx.HTTPError, ValueError):
            log.warning("coin prices unavailable")
        if not _px["usd"]:
            from app.services.fx.market import coin_usd  # noqa: PLC0415
            m = coin_usd()
            if m:
                _px["usd"] = {k: v for k, v in m.items() if k in CG}
                _px["at"] = time.time()
        if db is not None:
            try:
                from app.services.fx.service import get_anchor_rate  # noqa: PLC0415
                _px["rub"] = float(get_anchor_rate(db, "RUB"))
            except Exception:  # noqa: BLE001
                pass
    return _px["usd"], _px["rub"]


def value(token: str, amount: float, usd: dict, rub: float | None) -> tuple[float | None, float | None]:
    p = 1.0 if token in STABLE else usd.get(token)
    if p is None:
        return None, None
    u = amount * p
    return u, (u * rub if rub else None)


# ---------- лимиты и добавление ----------
def limit_of(user: models.User) -> int:
    from app.services.billing.entitlement import is_pro  # noqa: PLC0415
    if user.is_admin:
        return LIMITS["admin"]
    return LIMITS["pro"] if is_pro(user) else LIMITS["free"]


def view(w: WalletWatch, db: Session | None = None) -> dict:
    c = CHAINS[w.chain]
    out = {"id": w.id, "chain": w.chain, "net": c["net"], "chain_name": c["name"], "coin": c["coin"], "address": w.address,
           "label": w.label, "muted": w.muted, "min_usd": w.min_usd, "explorer": c["ex"], "addr_url": c["addr"].format(w.address),
           "last_check": w.last_check.isoformat() if w.last_check else None, "last_ok": w.last_ok.isoformat() if w.last_ok else None,
           "outgoing": bool(w.outgoing), "error": shown_error(w)}
    if db is not None:
        out["incoming"] = db.scalar(select(func.count(WalletTx.id)).where(WalletTx.watch_id == w.id, WalletTx.status == "ok",
                                                                           WalletTx.direction == "in")) or 0
        out["outgoing_n"] = db.scalar(select(func.count(WalletTx.id)).where(WalletTx.watch_id == w.id, WalletTx.status == "ok",
                                                                             WalletTx.direction == "out")) or 0
        out["hidden"] = db.scalar(select(func.count(WalletTx.id)).where(WalletTx.watch_id == w.id, WalletTx.status != "ok")) or 0
    return out


STALE = timedelta(minutes=20)


def shown_error(w: WalletWatch) -> str | None:
    """Разовый сбой источника не показываем: ошибка видна, только если удачной проверки не было дольше 20 минут."""
    if not w.last_error:
        return None
    ok = w.last_ok
    if ok is not None and ok.tzinfo is None:
        ok = ok.replace(tzinfo=timezone.utc)
    if ok is not None and datetime.now(timezone.utc) - ok < STALE:
        return None
    return w.last_error


def add(db: Session, user: models.User, address: str, label: str | None = None, chains: list[str] | None = None,
        reader: Reader | None = None) -> list[WalletWatch]:
    address = (address or "").strip()
    found = detect(address)
    if not found:
        raise WalletError("Не узнаю адрес. Поддерживаются: TRON (TRC-20), Bitcoin, Ethereum (ERC-20), BNB Chain (BEP-20), TON, Litecoin, Solana, Dogecoin.")
    use = [c for c in (chains or found) if c in found] or found
    addrs = set(db.scalars(select(WalletWatch.address).where(WalletWatch.user_id == user.id)))
    lim = limit_of(user)
    if not ({norm(c, address) for c in use} & addrs) and len(addrs) + 1 > lim:
        raise WalletError(f"Можно следить за {lim} {'кошельком' if lim == 1 else 'кошельками'} — сейчас уже {len(addrs)}. Удалите ненужный или напишите менеджеру.")
    out = []
    for c in use:
        a = norm(c, address)
        w = db.scalar(select(WalletWatch).where(WalletWatch.user_id == user.id, WalletWatch.chain == c, WalletWatch.address == a))
        if w is None:
            w = WalletWatch(user_id=user.id, chain=c, address=a, label=(label or "").strip()[:80] or None)
            db.add(w)
            db.flush()
            try:
                poll(db, w, reader or Reader(), first=True)
            except ChainError as e:
                w.last_error = str(e)[:300]
                w.last_ok = None
        elif label:
            w.label = label.strip()[:80]
        out.append(w)
    db.commit()
    return out


def owned(db: Session, user: models.User, wid: int) -> WalletWatch | None:
    w = db.get(WalletWatch, wid)
    return w if w and (w.user_id == user.id) else None


def history(db: Session, w: WalletWatch, hidden: bool = False, limit: int = 100) -> list[dict]:
    q = select(WalletTx).where(WalletTx.watch_id == w.id)
    if not hidden:
        q = q.where(WalletTx.status == "ok")
    c = CHAINS[w.chain]
    return [{"id": t.id, "txid": t.txid, "token": t.token, "amount": t.amount, "usd": t.usd, "rub": t.rub, "from": t.from_addr,
             "dir": t.direction or "in", "to": t.to_addr,
             "at": t.at.isoformat(), "status": t.status, "status_name": STATUS.get(t.status, t.status), "first": t.first,
             "url": c["tx"].format(t.txid) if not t.txid.startswith("bnb:") else c["addr"].format(w.address)}
            for t in db.scalars(q.order_by(WalletTx.at.desc(), WalletTx.id.desc()).limit(limit))]


# ---------- фильтры ----------
def looks_like(a: str, b: str) -> bool:
    return a != b and len(a) > 12 and len(b) > 12 and a[:4].lower() == b[:4].lower() and a[-4:].lower() == b[-4:].lower()


def classify(t: Transfer, usd: float | None, w: WalletWatch, known: set[str]) -> str:
    if t.flag in ("spam", "fake"):
        return t.flag
    if t.amount <= 0:
        return "zero"
    if t.dir == "in" and t.frm and any(looks_like(t.frm, k) for k in known):
        return "poison"
    if usd is not None and usd < (w.min_usd or 0):
        return "dust"
    if usd is None and t.token in STABLE and t.amount < (w.min_usd or 0):
        return "dust"
    return "ok"


# ---------- опрос ----------
def poll(db: Session, w: WalletWatch, reader: Reader, first: bool = False) -> list[WalletTx]:
    """Читает входящие и исходящие; новые строки сохраняет.

    first=True — история до подключения: без уведомлений, кроме поступлений за последние 3 часа
    (иначе перевод, пришедший за минуту до добавления адреса, прошёл бы молча)."""
    items = reader.incoming(w.chain, w.address, w)
    usd, rub = prices(db)
    usd = {**reader.px, **usd}
    have = set(db.scalars(select(WalletTx.uid).where(WalletTx.watch_id == w.id)))
    known = {x for x in db.scalars(select(WalletTx.from_addr).where(WalletTx.watch_id == w.id, WalletTx.status == "ok",
                                                                    WalletTx.direction == "in")) if x}
    recent = datetime.now(timezone.utc) - timedelta(hours=3)
    if first:
        items = [t for t in items if not t.uid.startswith("bnb:")]
    new = []
    for t in sorted(items, key=lambda x: x.at):
        if t.uid[:220] in have:
            continue
        u, r = value(t.token, t.amount, usd, rub)
        st = classify(t, u, w, known)
        at = t.at if t.at.tzinfo else t.at.replace(tzinfo=timezone.utc)
        if t.dir == "out" and not t.to and w.chain == "DOGE" and w.outgoing and not first:
            try:
                t.to = reader.doge_to(t.txid, w.address)
            except ChainError:
                pass
        quiet = st != "ok" or (t.dir == "out" and not w.outgoing) or (first and not (t.dir == "in" and at >= recent))
        row = WalletTx(watch_id=w.id, uid=t.uid[:220], txid=str(t.txid)[:160], token=t.token[:16], amount=t.amount, usd=u, rub=r,
                       from_addr=(t.frm or None) and t.frm[:128], to_addr=(t.to or None) and t.to[:128], direction=t.dir,
                       at=t.at, status=st, notified=quiet, first=first)
        db.add(row)
        have.add(row.uid)
        if st == "ok" and t.frm and t.dir == "in":
            known.add(t.frm)
        new.append(row)
        if st in ("poison", "fake") and not first:
            from app.services import activity as act  # noqa: PLC0415
            act.record(db, source="wallet", action=st, user_id=w.user_id,
                       label=f"{STATUS[st]}, скрыто: {t.amount:g} {t.token} от {t.frm or '—'} на {w.chain} {w.address}",
                       meta={"watch": w.id, "tx": t.txid, "from": t.frm})
    w.last_check = w.last_ok = datetime.now(timezone.utc)
    w.last_error = None
    db.flush()
    return new


def money(x: float, token: str) -> str:
    d = 2 if token in STABLE or x >= 100 else (4 if x >= 1 else 8)
    s = f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    if "," in s and token not in STABLE:
        s = s.rstrip("0").rstrip(",")
    return s


def _net(w: WalletWatch, t: WalletTx) -> str:
    c = CHAINS[w.chain]
    return c["net"] if t.token != c["coin"] or w.chain == "TRON" else c["name"]


def _fiat(t: WalletTx) -> str | None:
    if t.rub:
        return f"≈ {t.rub:,.0f} ₽".replace(",", " ")
    if t.usd:
        return f"≈ ${t.usd:,.2f}".replace(",", " ")
    return None


def _wname(w: WalletWatch) -> str:
    return w.label or w.address


def alert_text(w: WalletWatch, t: WalletTx) -> str:
    """Обычное HTML-сообщение (запасной вариант и журнал)."""
    out = (t.direction or "in") == "out"
    sign, dot = ("−", "🔴") if out else ("+", "🟢")
    lines = [f"{dot} <b>{sign}{money(t.amount, t.token)} {html.escape(t.token)}</b> · {_net(w, t)}"]
    if f := _fiat(t):
        lines.append(f)
    lines.append(f"{'С кошелька' if out else 'Кошелёк'}: {html.escape(_wname(w))}")
    if out:
        lines.append(f"Куда: <code>{html.escape(t.to_addr)}</code>" if t.to_addr else "Куда: — (сеть не показывает получателя)")
    else:
        lines.append(f"От: <code>{html.escape(t.from_addr)}</code>" if t.from_addr else "От: — (сеть не показывает отправителя)")
    lines.append(t.at.astimezone(MSK).strftime("%d.%m %H:%M МСК"))
    if t.first:
        lines.append("<i>Пришло незадолго до начала наблюдения.</i>")
    return "\n".join(lines)


def _cut(a: str | None, n: int = 8) -> str:
    return f"{a[:n]}…{a[-6:]}" if a and len(a) > n + 8 else (a or "—")


def day_totals(db: Session, w: WalletWatch, token: str, now: datetime | None = None) -> tuple[float, float]:
    """Сколько этого токена пришло и ушло за последние сутки (без скрытых)."""
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=24)
    rows = db.execute(select(WalletTx.direction, func.sum(WalletTx.amount)).where(
        WalletTx.watch_id == w.id, WalletTx.token == token, WalletTx.status == "ok", WalletTx.at >= since).group_by(WalletTx.direction)).all()
    d = {k or "in": float(v or 0) for k, v in rows}
    return d.get("in", 0.0), d.get("out", 0.0)


def rate_line(token: str, coin: str, usd: dict, rub: float | None) -> str | None:
    """Строка курса в рублях для подписи карточки (без названия источника)."""
    if not rub:
        return None
    parts = []
    for tk in dict.fromkeys([token, coin]):
        p = 1.0 if tk in STABLE else usd.get(tk)
        if p:
            v = p * rub
            parts.append(f"1 {tk} = {v:,.2f} ₽".replace(",", " ").replace(".", ",") if v < 1000 else f"1 {tk} = {v:,.0f} ₽".replace(",", " "))
    return " · ".join(parts) or None


def alert_rich(w: WalletWatch, t: WalletTx, db: Session | None = None, check: str | None = None) -> str:
    """Карточка перевода в формате rich: сумма в заголовке, таблица с полными адресами, кнопки внутри, курс в подписи."""
    out = (t.direction or "in") == "out"
    e = html.escape
    c = CHAINS[w.chain]
    head = f"<h3>{'🔴 −' if out else '🟢 +'}{e(money(t.amount, t.token))} {e(t.token)} · {e(_net(w, t))}</h3>"
    peer = t.to_addr if out else t.from_addr
    rows = []
    if t.rub:
        rows.append(("≈ в рублях", f"<b>{t.rub:,.0f} ₽</b>".replace(",", " ")))
    elif t.usd:
        rows.append(("≈ в долларах", f"<b>${t.usd:,.2f}</b>".replace(",", " ")))
    rows += [(f"<b>{e(w.label or ('Кошелёк ' + c['name']))}</b>", f"<code>{e(w.address)}</code>"),
             ("Куда" if out else "От", f"<code>{e(peer)}</code>" if peer else "сеть не показывает"),
             ("Когда", t.at.astimezone(MSK).strftime("%d.%m %H:%M МСК"))]
    if check:
        rows.append(("Проверка", e(check)))
    if t.first:
        rows.append(("Важно", "пришло до начала наблюдения"))
    table = "<table bordered striped compact>" + "".join(
        f"<tr><td>{a}</td><td align=\"right\">{b}</td></tr>" for a, b in rows) + "</table>"
    more = ""
    if db is not None:
        i, o = day_totals(db, w, t.token)
        more = (f"<details><summary>За сутки по {e(t.token)}</summary><table compact striped>"
                f"<tr><td>Пришло</td><td align=\"right\">+{e(money(i, t.token))}</td></tr>"
                f"<tr><td>Ушло</td><td align=\"right\">−{e(money(o, t.token))}</td></tr></table></details>")
    url = c["addr"].format(w.address) if t.txid.startswith("bnb:") else c["tx"].format(t.txid)
    btn = [f'<tg-button type="url" style="{"danger" if out else "primary"}" url="{e(url)}">🔎 {e(c["ex"])}</tg-button>']
    if peer:
        btn.append(f'<tg-button type="copy_text" text="{e(peer)}">📋 {"Куда" if out else "Отправитель"}</tg-button>')
    row2 = []
    if peer:
        row2.append(f'<tg-button type="url" url="{SITE}#wallets/aml/{w.chain}/{e(peer)}">🛡 AML</tg-button>')
    row2.append('<tg-button type="callback_data" style="link" data="wal:open">👛 Кошельки</tg-button>')
    row2.append(f'<tg-button type="callback_data" style="link" data="wal:mute:{w.id}">🔕 Тише</tg-button>')
    buttons_ = f"<tg-button-row>{''.join(btn)}</tg-button-row><tg-button-row>{''.join(row2)}</tg-button-row>"
    usd, rub = prices(db)
    rl = rate_line(t.token, c["coin"], usd, rub)
    return head + table + more + buttons_ + (f"<footer>{e(rl)}</footer>" if rl else "")


def sender_check(w: WalletWatch, t: WalletTx, reader: Reader | None = None) -> str | None:
    """Быстрая открытая проверка контрагента для стейблов: чёрный список Tether / Circle (TRON, Ethereum)."""
    peer = t.to_addr if (t.direction or "in") == "out" else t.from_addr
    if not peer or t.token not in STABLE or w.chain not in ("TRON", "ETH"):
        return None
    try:
        hits = (reader or Reader()).blacklisted(w.chain, peer)
    except ChainError:
        return None
    return ("⛔ в чёрном списке: " + ", ".join(hits)) if hits else "✅ не в чёрном списке эмитента"


def buttons(w: WalletWatch, t: WalletTx) -> list:
    c = CHAINS[w.chain]
    url = c["addr"].format(w.address) if t.txid.startswith("bnb:") else c["tx"].format(t.txid)
    row = [{"text": f"🔎 {c['ex']}", "url": url}]
    peer = t.to_addr if (t.direction or "in") == "out" else t.from_addr
    if peer:
        row.append({"text": "🛡 Проверить AML", "url": f"{SITE}#wallets/aml/{w.chain}/{peer}"})
    return [row, [{"text": "👛 Кошельки", "url": f"{SITE}#wallets"}, {"text": "🔕 Не уведомлять", "callback_data": f"wal:mute:{w.id}"}]]


def send(chat_id: int, text: str, kb: list, rich: str | None = None) -> bool:
    """rich-карточка несёт кнопки внутри себя; клавиатура нужна только обычному запасному сообщению."""
    from app.services import tg  # noqa: PLC0415
    return tg.send(chat_id, text, kb, rich=rich, rich_kb=None)


def run_wallets(db: Session, reader: Reader | None = None, sender=send, now_ts: float | None = None) -> tuple[int, int]:
    """Один проход: опросить все кошельки, разослать новые поступления (во сне — копятся до утра)."""
    from app.services.notify.service import get_prefs, in_sleep  # noqa: PLC0415

    reader = reader or Reader()
    checked = sent = 0
    order = {c: i for i, c in enumerate(ORDER)}
    ids = [w.id for w in sorted(db.scalars(select(WalletWatch)).all(), key=lambda x: (order.get(x.chain, 9), x.id))]
    for wid in ids:
        # Кошелёк могли удалить, пока шёл проход, — тогда просто пропускаем.
        try:
            w = db.get(WalletWatch, wid)
            if w is None:
                continue
            poll(db, w, reader)
            db.commit()
            checked += 1
        except ChainError as e:
            db.rollback()
            w = db.get(WalletWatch, wid)
            if w is not None:
                w.last_error, w.last_check = str(e)[:300], datetime.now(timezone.utc)
                log.info("wallet %s %s: %s", wid, w.chain, e)
                db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            w = db.get(WalletWatch, wid)
            if w is None:
                continue
            log.exception("wallet %s failed", wid)
            w.last_error = f"ошибка чтения: {type(e).__name__}"
            db.commit()
    now_msk = datetime.fromtimestamp(now_ts or time.time(), MSK)
    pend = db.execute(select(WalletTx, WalletWatch).join(WalletWatch, WalletWatch.id == WalletTx.watch_id)
                      .where(WalletTx.notified.is_(False), WalletTx.status == "ok").order_by(WalletTx.at)).all()
    for t, w in pend:
        u = db.get(models.User, w.user_id)
        tg = u.telegram_user_id if u else None
        if w.muted or not tg:
            t.notified = True
            continue
        prefs = get_prefs(db, tg)
        if in_sleep(prefs, now_msk):
            continue
        txt = alert_text(w, t)
        if sender is send:
            ok = sender(tg, txt, buttons(w, t), rich=alert_rich(w, t, db, sender_check(w, t, reader)))
        else:
            ok = sender(tg, txt, buttons(w, t))
        if ok:
            t.notified = True
            sent += 1
            from app.services.notify.service import _log_sent  # noqa: PLC0415
            _log_sent(db, tg, "wallet", txt)
    db.commit()
    return checked, sent


def aml(chain: str, addr: str, reader: Reader | None = None) -> dict:
    """Открытая проверка отправителя: формат адреса, чёрные списки эмитентов стейблкоинов."""
    reader = reader or Reader()
    ok = chain in detect(addr)
    out = {"chain": chain, "address": addr, "valid": ok, "checks": [], "explorer": CHAINS.get(chain, {}).get("addr", "{}").format(addr)}
    if not ok:
        return out
    if chain in ("ETH", "TRON"):
        try:
            hits = reader.blacklisted(chain, addr)
            out["checks"].append({"name": "Чёрный список Tether / Circle", "bad": bool(hits),
                                  "text": ("заблокирован: " + ", ".join(hits)) if hits else "не заблокирован"})
        except ChainError as e:
            out["checks"].append({"name": "Чёрный список Tether / Circle", "bad": None, "text": f"не проверено: {e}"})
    return out
