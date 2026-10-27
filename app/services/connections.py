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
}
LIVE = {"viabtc"}  # эти пулы Kirka уже читает сама
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
    """Входит в CROFT и собирает короткую сводку. Бросает ValueError с понятным текстом."""
    import httpx  # noqa: PLC0415
    cli = (factory or _croft_client)(login, password)
    try:
        groups = cli.view_groups() or []
        customer = next((g["customer"]["uniqId"] for g in groups if isinstance(g, dict) and g.get("customer")), None)
        if not customer:
            raise ValueError("В этом CROFT нет доступной фермы (нет просмотра клиента)")
        raw = cli.devices(customer) or []
    except ValueError:
        raise
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code in (400, 401, 403):
            raise ValueError("CROFT не принял логин или пароль") from e
        raise ValueError("CROFT сейчас не отвечает, попробуйте позже") from e
    except Exception as e:  # noqa: BLE001 — сеть, таймаут, формат
        raise ValueError("CROFT сейчас не отвечает, попробуйте позже") from e
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
        raise ValueError("Нужны логин и пароль CROFT")
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
            raise ValueError("Пароль не читается — подключите CROFT заново")
        acc.stats, acc.status, acc.error = croft_probe(acc.login, pw, factory), "ok", None
    except ValueError as e:
        acc.status, acc.error = "error", str(e)
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
    private = not key and any(x in path for x in ("/user", "/dashboard", "/account", "/my", "/settings", "/login"))
    return {"provider": prov, "name": POOLS[prov][0], "url": url, "key": key, "coin": coin,
            "private": private, "live": prov in LIVE}


def pool_connect(db: Session, user: models.User, text: str, cli=None) -> dict:
    """Добавляет ссылку наблюдателя. Для ViaBTC сразу проверяет и читает хешрейт."""
    r = recognize(text)
    if not r:
        raise ValueError("Не узнала ссылку пула. Пришлите ссылку наблюдателя (observer) из кабинета пула.")
    if r["private"] or not r["key"]:
        raise ValueError(f"Это ссылка на личный кабинет {r['name']}, по ней ничего не видно без пароля. "
                         "Нужна ссылка наблюдателя: в кабинете пула раздел «Наблюдатель» / Observer / Watcher → «Создать ссылку».")
    coin = r["coin"] or "BTC"
    q = select(pm.PoolAccount).where(pm.PoolAccount.provider == r["provider"], pm.PoolAccount.access_key == r["key"],
                                     pm.PoolAccount.coin == coin)
    ex = db.scalars(q).first()
    if ex is not None:
        if ex.owner_user_id not in (None, user.id):
            raise ValueError("Эта ссылка уже подключена у другого человека.")
        if ex.owner_user_id == user.id:
            return {"account": pool_view(ex), "found": True, "already": True, **r}
        # общая ссылка оператора — отдаём человеку копией не будем: просто сообщаем
        return {"account": pool_view(ex), "found": True, "already": True, "shared": True, **r}
    a = pm.PoolAccount(provider=r["provider"], access_key=r["key"], coin=coin, sub_user_id=0, url=r["url"],
                       owner_user_id=user.id, active=True, track=True,
                       note_user=user.handle)
    found = False
    if r["live"]:
        from app.services.pools.sync import sync_account  # noqa: PLC0415
        from app.services.pools.viabtc import ViaBTC  # noqa: PLC0415
        cli = cli or ViaBTC()
        try:
            inf = cli.info(r["key"])
        except Exception as e:  # noqa: BLE001
            raise ValueError("ViaBTC не открывает эту ссылку — проверьте, что она не отозвана.") from e
        a.account = (inf or {}).get("account")
        db.add(a)
        db.flush()
        try:
            sync_account(db, cli, a, with_workers=True, with_profit=False)
            found = True
        except Exception as e:  # noqa: BLE001
            log.warning("pool first sync failed: %s", e)
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
