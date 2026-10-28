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
STATUS = {"ok": "поступление", "dust": "меньше порога", "spam": "спам-токен", "fake": "поддельный токен",
          "poison": "отравление адреса", "zero": "нулевой перевод"}


class WalletError(RuntimeError):
    pass


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
           "last_check": w.last_check.isoformat() if w.last_check else None, "error": w.last_error}
    if db is not None:
        out["incoming"] = db.scalar(select(func.count(WalletTx.id)).where(WalletTx.watch_id == w.id, WalletTx.status == "ok")) or 0
        out["hidden"] = db.scalar(select(func.count(WalletTx.id)).where(WalletTx.watch_id == w.id, WalletTx.status != "ok")) or 0
    return out


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
    if t.frm and any(looks_like(t.frm, k) for k in known):
        return "poison"
    if usd is not None and usd < (w.min_usd or 0):
        return "dust"
    if usd is None and t.token in STABLE and t.amount < (w.min_usd or 0):
        return "dust"
    return "ok"


# ---------- опрос ----------
def poll(db: Session, w: WalletWatch, reader: Reader, first: bool = False) -> list[WalletTx]:
    """Читает входящие; новые строки сохраняет. first=True — история до подключения, без уведомлений."""
    items = reader.incoming(w.chain, w.address, w)
    usd, rub = prices(db)
    usd = {**reader.px, **usd}
    have = set(db.scalars(select(WalletTx.uid).where(WalletTx.watch_id == w.id)))
    known = {x for x in db.scalars(select(WalletTx.from_addr).where(WalletTx.watch_id == w.id, WalletTx.status == "ok")) if x}
    if first:
        items = [t for t in items if not t.uid.startswith("bnb:")]
    new = []
    for t in sorted(items, key=lambda x: x.at):
        if t.uid[:220] in have:
            continue
        u, r = value(t.token, t.amount, usd, rub)
        st = classify(t, u, w, known)
        row = WalletTx(watch_id=w.id, uid=t.uid[:220], txid=str(t.txid)[:160], token=t.token[:16], amount=t.amount, usd=u, rub=r,
                       from_addr=(t.frm or None) and t.frm[:128], at=t.at, status=st, notified=first or st != "ok", first=first)
        db.add(row)
        have.add(row.uid)
        if st == "ok" and t.frm:
            known.add(t.frm)
        new.append(row)
        if st in ("poison", "fake") and not first:
            from app.services import activity as act  # noqa: PLC0415
            act.record(db, source="wallet", action=st, user_id=w.user_id,
                       label=f"{STATUS[st]}, скрыто: {t.amount:g} {t.token} от {t.frm or '—'} на {w.chain} {w.address}",
                       meta={"watch": w.id, "tx": t.txid, "from": t.frm})
    w.last_check = datetime.now(timezone.utc)
    w.last_error = None
    db.flush()
    return new


def money(x: float, token: str) -> str:
    d = 2 if token in STABLE or x >= 100 else (4 if x >= 1 else 8)
    s = f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    if "," in s and token not in STABLE:
        s = s.rstrip("0").rstrip(",")
    return s


def alert_text(w: WalletWatch, t: WalletTx) -> str:
    c = CHAINS[w.chain]
    net = c["net"] if t.token != c["coin"] or w.chain == "TRON" else c["name"]
    lines = [f"🟢 <b>+{money(t.amount, t.token)} {html.escape(t.token)}</b> · {net}"]
    if t.rub:
        lines.append(f"≈ {t.rub:,.0f} ₽".replace(",", " "))
    elif t.usd:
        lines.append(f"≈ ${t.usd:,.2f}".replace(",", " "))
    lines.append(f"Кошелёк: {html.escape(w.label or w.address[:6] + '…' + w.address[-4:])}")
    lines.append(f"От: <code>{html.escape(t.from_addr)}</code>" if t.from_addr else "От: — (сеть не показывает отправителя)")
    lines.append(t.at.astimezone(MSK).strftime("%d.%m %H:%M МСК"))
    return "\n".join(lines)


def buttons(w: WalletWatch, t: WalletTx) -> list:
    c = CHAINS[w.chain]
    url = c["addr"].format(w.address) if t.txid.startswith("bnb:") else c["tx"].format(t.txid)
    row = [{"text": f"🔎 {c['ex']}", "url": url}]
    if t.from_addr:
        row.append({"text": "🛡 Проверить AML", "url": f"{SITE}#wallets/aml/{w.chain}/{t.from_addr}"})
    return [row, [{"text": "👛 Кошельки", "url": f"{SITE}#wallets"}, {"text": "🔕 Не уведомлять", "callback_data": f"wal:mute:{w.id}"}]]


def send(chat_id: int, text: str, kb: list) -> bool:
    from app.core.config import get_settings  # noqa: PLC0415
    tok = get_settings().bot_token
    if not tok:
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=15,
                       json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
                             "reply_markup": {"inline_keyboard": kb}})
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def run_wallets(db: Session, reader: Reader | None = None, sender=send, now_ts: float | None = None) -> tuple[int, int]:
    """Один проход: опросить все кошельки, разослать новые поступления (во сне — копятся до утра)."""
    from app.services.notify.service import get_prefs, in_sleep  # noqa: PLC0415

    reader = reader or Reader()
    checked = sent = 0
    order = {c: i for i, c in enumerate(ORDER)}
    for w in sorted(db.scalars(select(WalletWatch)).all(), key=lambda x: (order.get(x.chain, 9), x.id)):
        try:
            poll(db, w, reader)
            db.commit()
            checked += 1
        except ChainError as e:
            db.rollback()
            w = db.get(WalletWatch, w.id)
            w.last_error, w.last_check = str(e)[:300], datetime.now(timezone.utc)
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.exception("wallet %s failed", w.id)
            w = db.get(WalletWatch, w.id)
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
        if sender(tg, txt, buttons(w, t)):
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
