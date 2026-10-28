"""Подключения людей: CROFT (логин/пароль, только чтение) и ссылки наблюдателя пулов.

Что жмёшь — то и получаешь: при подключении сразу проверяем доступ и показываем,
что Kirka увидела (асики, площадки, хешрейт / воркеры пула). Пароль CROFT хранится
зашифрованным ключом сервера и никогда не отдаётся обратно.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db import pool_models as pm
from app.db.activity_models import CroftAccount
from app.services import activity as act

log = logging.getLogger(__name__)


class ConnError(ValueError):
    """Ошибка подключения с готовым текстом для человека (без деталей исключений)."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text

POOLS = {
    "viabtc": ("ViaBTC", ("viabtc.com", "viabtc.net")),
    "antpool": ("Antpool", ("antpool.com",)),
    "f2pool": ("F2Pool", ("f2pool.com",)),
    "emcd": ("EMCD", ("emcd.io", "emcd.pro")),
    "nicehash": ("NiceHash", ("nicehash.com",)),
    "luxor": ("Luxor", ("luxor.tech",)),
    "binance": ("Binance Pool", ("binance.com",)),
    "foundry": ("Foundry", ("foundryusapool.com", "foundrydigital.com")),
    "braiins": ("Braiins", ("braiins.com", "slushpool.com")),
    "secpool": ("SECPOOL", ("secpool.com",)),
    "spiderpool": ("SpiderPool", ("spiderpool.com",)),
    "poolin": ("Poolin", ("poolin.com",)),
    "litecoinpool": ("Litecoinpool", ("litecoinpool.org",)),
    "trustpool": ("Trustpool", ("trustpool.cc", "trustpool.ru")),
    "2miners": ("2Miners", ("2miners.com",)),
    "bitfufu": ("BitFuFu", ("bitfufu.net", "bitfufu.com")),
}
LIVE = {"viabtc", "trustpool", "antpool", "f2pool", "emcd", "binance", "2miners", "bitfufu", "nicehash"}  # эти пулы Kirka читает сама
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


# ---------- шифрование ----------
def _fernet() -> Fernet:
    key = hashlib.sha256(("kirka-conn:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def enc(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


def dec(s: str) -> str | None:
    try:
        return _fernet().decrypt(s.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ---------- CROFT ----------
def _croft_client(login: str, password: str):
    from app.services.farm.croft import CroftClient  # noqa: PLC0415
    return CroftClient(get_settings().croft_base_url, login, password)


def croft_probe(login: str, password: str, factory=None) -> dict:
    """Входит в CROFT и собирает короткую сводку. Бросает ConnError с понятным текстом."""
    import httpx  # noqa: PLC0415
    cli = (factory or _croft_client)(login, password)
    try:
        groups = cli.view_groups() or []
        customer = next((g["customer"]["uniqId"] for g in groups if isinstance(g, dict) and g.get("customer")), None)
        if not customer:
            raise ConnError("В этом CROFT нет доступной фермы (нет просмотра клиента)")
        raw = cli.devices(customer) or []
    except ConnError:
        raise
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code in (400, 401, 403):
            raise ConnError("CROFT не принял логин или пароль") from e
        raise ConnError("CROFT сейчас не отвечает, попробуйте позже") from e
    except Exception as e:  # noqa: BLE001 — сеть, таймаут, формат
        raise ConnError("CROFT сейчас не отвечает, попробуйте позже") from e
    sites: dict[str, dict] = {}
    working = offline = 0
    ths = 0.0
    for d in raw:
        if not isinstance(d, dict):
            continue
        site = ((d.get("platform") or {}).get("name") or "Без площадки").strip()
        cont = ((d.get("container") or {}).get("name") or "").strip()
        s = sites.setdefault(site, {"name": site, "containers": set(), "devices": 0, "working": 0, "offline": 0})
        if cont:
            s["containers"].add(cont)
        s["devices"] += 1
        st = d.get("status") or "unknown"
        if st == "working":
            working += 1
            s["working"] += 1
            try:
                ths += float(d.get("hashRateMHS") or 0) / 1e6
            except (TypeError, ValueError):
                pass
        elif st == "offline":
            offline += 1
            s["offline"] += 1
    out = [{**v, "containers": len(v["containers"])} for v in sites.values()]
    out.sort(key=lambda x: -x["devices"])
    return {"devices": len(raw), "working": working, "offline": offline, "other": len(raw) - working - offline,
            "hashrate_ths": round(ths, 1), "sites": out[:30]}


def croft_connect(db: Session, user: models.User, login: str, password: str, factory=None) -> CroftAccount:
    login, password = (login or "").strip(), (password or "").strip()
    if not login or not password:
        raise ConnError("Нужны логин и пароль CROFT")
    stats = croft_probe(login, password, factory)
    acc = db.scalar(select(CroftAccount).where(CroftAccount.user_id == user.id, CroftAccount.login == login))
    if acc is None:
        acc = CroftAccount(user_id=user.id, login=login, secret=enc(password))
        db.add(acc)
    acc.secret, acc.status, acc.error, acc.stats, acc.checked_at = enc(password), "ok", None, stats, datetime.now(UTC)
    db.commit()
    act.record(db, source="site", action="conn:croft", user_id=user.id,
               label=f"Подключил CROFT ({login}): {stats['devices']} асиков")
    return acc


def croft_recheck(db: Session, acc: CroftAccount, factory=None) -> CroftAccount:
    pw = dec(acc.secret)
    try:
        if not pw:
            raise ConnError("Пароль не читается — подключите CROFT заново")
        acc.stats, acc.status, acc.error = croft_probe(acc.login, pw, factory), "ok", None
    except ConnError as e:
        acc.status, acc.error = "error", e.text
    acc.checked_at = datetime.now(UTC)
    db.commit()
    return acc


def croft_view(a: CroftAccount) -> dict:
    return {"id": a.id, "login": a.login, "status": a.status, "error": a.error, "stats": a.stats or {},
            "checked_at": act._iso(a.checked_at), "created_at": act._iso(a.created_at)}


# ---------- ссылки наблюдателя ----------
def recognize(text: str) -> dict | None:
    """Ищет в тексте ссылку пула. → {provider, name, url, key, coin, private, live}."""
    m = URL_RE.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,;)")
    u = urlparse(url)
    host = (u.hostname or "").lower()
    prov = next((k for k, (_, doms) in POOLS.items() if any(host == d or host.endswith("." + d) for d in doms)), None)
    if not prov:
        return None
    q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
    key = q.get("access_key") or q.get("accesskey") or q.get("observer_token") or q.get("token") or q.get("key")
    coin = (q.get("coin") or q.get("coin_type") or q.get("currency") or "").upper() or None
    path = u.path.lower()
    if prov == "f2pool" and not key:
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2 and parts[0].lower() in ("btc", "ltc", "doge", "kas", "etc", "bch", "zec", "dash"):
            coin, key = parts[0].upper(), parts[1]
    if prov == "nicehash" and not key:
        mm = re.search(r"/(?:my/)?miner/([0-9a-f-]{8,})", path)
        key = mm.group(1) if mm else None
    if prov in ("emcd", "luxor", "braiins", "binance") and not key:
        mm = re.search(r"/(?:watcher|observer|share|s)/([A-Za-z0-9_-]{6,})", u.path)
        key = mm.group(1) if mm else None
    acc = None
    if prov in LIVE and prov not in ("viabtc", "trustpool"):
        from app.services.pools.readers import reader  # noqa: PLC0415
        p = reader(prov).parse(url)
        if p:
            key, coin, acc = p["key"], p.get("coin") or coin, p.get("account")
        elif prov not in ("nicehash", "f2pool"):
            key = None
    private = not key and any(x in path for x in ("/user", "/dashboard", "/account", "/my", "/settings", "/login"))
    return {"provider": prov, "name": POOLS[prov][0], "url": url, "key": key, "coin": coin,
            "private": private, "live": prov in LIVE, "login": acc}


def pool_connect(db: Session, user: models.User, text: str, cli=None, shared: bool = False, label: str | None = None) -> dict:
    """Добавляет ссылку наблюдателя. Для ViaBTC сразу проверяет и читает хешрейт."""
    r = recognize(text)
    if not r:
        raise ConnError("Не узнала ссылку пула. Пришлите ссылку наблюдателя (observer) из кабинета пула.")
    if r["private"] or not r["key"]:
        raise ConnError(f"Это ссылка на личный кабинет {r['name']}, по ней ничего не видно без пароля. "
                         "Нужна ссылка наблюдателя: в кабинете пула раздел «Наблюдатель» / Observer / Watcher → «Создать ссылку».")
    coin = r["coin"] or ("" if r["provider"] in ("emcd", "binance") else "BTC")
    if not coin and r["live"]:  # EMCD / Binance: the coin is known only from the pool
        from app.services.pools.readers import PoolError, reader  # noqa: PLC0415
        try:
            coin = (reader(r["provider"]).read(r["url"], r["key"], None, False, False).coin or "BTC").upper()
        except PoolError as e:
            raise ConnError(f"{r['name']} не открывает эту ссылку: {e}") from e
    coin = coin or "BTC"
    q = select(pm.PoolAccount).where(pm.PoolAccount.provider == r["provider"], pm.PoolAccount.access_key == r["key"],
                                     pm.PoolAccount.coin == coin)
    ex = db.scalars(q).first()
    if ex is not None:
        if shared or ex.owner_user_id is None and user.is_admin:
            return {"account": pool_view(ex), "found": True, "already": True, **r}
        if ex.owner_user_id not in (None, user.id):
            raise ConnError("Эта ссылка уже подключена у другого человека.")
        if ex.owner_user_id == user.id:
            return {"account": pool_view(ex), "found": True, "already": True, **r}
        return {"account": pool_view(ex), "found": True, "already": True, "shared": True, **r}
    a = pm.PoolAccount(provider=r["provider"], access_key=r["key"][:128], coin=coin, sub_user_id=0, url=r["url"],
                       owner_user_id=None if shared else user.id, active=True, track=True, account=(r.get("login") or None),
                       note_user=(label or "").strip()[:120] or (None if shared else user.handle))
    found = False
    if r["live"]:
        from app.services.pools.sync import sync_account, sync_any, via_client  # noqa: PLC0415
        if r["provider"] in ("viabtc", "trustpool"):
            vcli = cli or via_client(r["provider"])
            try:
                inf = vcli.info(r["key"])
            except Exception as e:  # noqa: BLE001
                raise ConnError(f"{r['name']} не открывает эту ссылку — проверьте, что она не отозвана.") from e
            a.account = (inf or {}).get("account")
            db.add(a)
            db.flush()
            try:
                sync_account(db, vcli, a, with_workers=True, with_profit=False)
                found = True
            except Exception as e:  # noqa: BLE001
                log.warning("pool first sync failed: %s", e)
        else:
            db.add(a)
            db.flush()
            try:
                sync_any(db, a, with_workers=True, with_profit=True)
                found = True
            except Exception as e:  # noqa: BLE001
                db.rollback()
                raise ConnError(f"{r['name']} не открывает эту ссылку: {e}") from e
    else:
        db.add(a)
    db.commit()
    act.record(db, source="site", action="conn:pool", user_id=user.id,
               label=f"Добавил ссылку наблюдателя {r['name']}" + (f" {coin}" if r["coin"] else ""))
    return {"account": pool_view(a), "found": found, "already": False, **r}


def pool_view(a: pm.PoolAccount) -> dict:
    name = POOLS.get(a.provider, (a.provider,))[0]
    return {"id": a.id, "provider": a.provider, "name": name, "coin": a.coin, "account": a.account,
            "url": a.url, "live": a.provider in LIVE, "hs_1h": a.hs_1h, "hs_1d": a.hs_1d,
            "workers_active": a.workers_active, "workers_inactive": a.workers_inactive,
            "synced_at": act._iso(a.last_ok), "error": a.last_error, "note": a.note}


# ---------- сводка ----------
def server_croft() -> dict | None:
    s = get_settings()
    return {"login": s.croft_login} if s.croft_login else None


def of_user(db: Session, user: models.User) -> dict:
    crofts = [croft_view(a) for a in db.scalars(select(CroftAccount).where(CroftAccount.user_id == user.id).order_by(CroftAccount.id))]
    pools = [pool_view(a) for a in db.scalars(select(pm.PoolAccount).where(pm.PoolAccount.owner_user_id == user.id).order_by(pm.PoolAccount.id))]
    out = {"croft": crofts, "pools": pools}
    if user.is_admin:
        from app.db import farm_models as fm  # noqa: PLC0415
        from sqlalchemy import func  # noqa: PLC0415
        sc = server_croft()
        if sc:
            sc["devices"] = db.scalar(select(func.count(fm.FarmDevice.id))) or 0
            sc["sites"] = db.scalar(select(func.count(fm.FarmSite.id))) or 0
        out["server"] = {"croft": sc, "pools": db.scalar(select(func.count(pm.PoolAccount.id)).where(pm.PoolAccount.owner_user_id.is_(None))) or 0}
    return out


def summary_all(db: Session) -> dict[int, dict]:
    """user_id → {croft: [...], pools: {name: n}} для таблицы админки."""
    out: dict[int, dict] = {}
    for a in db.scalars(select(CroftAccount)):
        out.setdefault(a.user_id, {"croft": [], "pools": {}})["croft"].append(
            {"login": a.login, "status": a.status, "devices": (a.stats or {}).get("devices"), "error": a.error,
             "checked_at": act._iso(a.checked_at)})
    for a in db.scalars(select(pm.PoolAccount).where(pm.PoolAccount.owner_user_id.is_not(None))):
        p = out.setdefault(a.owner_user_id, {"croft": [], "pools": {}})["pools"]
        n = POOLS.get(a.provider, (a.provider,))[0]
        p[n] = p.get(n, 0) + 1
    return out


# ---------- уведомление менеджеров ----------
def notify_managers(db: Session, user: models.User, text: str, sender=None) -> int:
    """Пишет менеджерам: кто что подключил. Самому себе не пишем."""
    from app.services import support as sup  # noqa: PLC0415
    send = sender or (lambda chat, t: sup.tg_send(chat, t, reply_button=False))
    n = 0
    for m in sup.managers(db):
        if m == user.telegram_user_id:
            continue
        if send(m, f"🔌 {user.handle}: {text}"):
            n += 1
    return n


def hs_text(ths: float) -> str:
    return f"{ths / 1000:.1f} PH/s".replace(".", ",") if ths >= 1000 else f"{ths:.1f} TH/s".replace(".", ",")


def croft_text(st: dict) -> str:
    s = f"{st.get('devices', 0)} асиков, в работе {st.get('working', 0)}, не на связи {st.get('offline', 0)}"
    if st.get("hashrate_ths"):
        s += ", " + hs_text(st["hashrate_ths"])
    sites = st.get("sites") or []
    if sites:
        s += "\nПлощадки: " + ", ".join(f"{x['name']} ({x['devices']})" for x in sites[:6])
    return s


def delete_croft(db: Session, user: models.User, acc_id: int) -> bool:
    a = db.get(CroftAccount, acc_id)
    if not a or a.user_id != user.id:
        return False
    db.delete(a)
    db.commit()
    return True


def delete_pool(db: Session, user: models.User, acc_id: int) -> bool:
    a = db.get(pm.PoolAccount, acc_id)
    if not a or a.owner_user_id != user.id:
        return False
    db.delete(a)
    db.commit()
    return True
