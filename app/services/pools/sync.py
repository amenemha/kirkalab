"""Pool sync: snapshot, hourly archive, daily accruals and workers for every active account."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import pool_models as pm
from app.services.pools.viabtc import ViaBTC, ViaBTCError, hs, num

log = logging.getLogger("kirka.pools")


def _ts(x) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(x), tz=timezone.utc) if x else None
    except (TypeError, ValueError):
        return None


def sync_account(db: Session, cli: ViaBTC, a: pm.PoolAccount, with_workers: bool = True, with_profit: bool = True) -> None:
    key, coin, uid = a.access_key, a.coin, a.sub_user_id or 0
    h = cli.home(key, coin, uid)
    a.hs_10m, a.hs_1h, a.hs_1d = hs(h.get("hashrate_10min")), hs(h.get("hashrate_1hour")), hs(h.get("hashrate_1day"))
    a.workers_active, a.workers_inactive, a.workers_invalid = h.get("total_active"), h.get("total_unactive"), h.get("total_invalid")
    a.balance, a.profit_24h, a.profit_total = num(h.get("account_balance")), num(h.get("profit_24hour")), num(h.get("profit_total"))

    hourly = cli.hourly(key, coin, uid)
    have = {r.ts: r for r in db.scalars(select(pm.PoolHourly).where(pm.PoolHourly.account_id == a.id, pm.PoolHourly.ts.in_(list(hourly) or [0])))}
    now = datetime.now(timezone.utc).timestamp()
    for ts, (v, w, rj) in hourly.items():
        if ts + 3600 > now:  # current hour is not closed yet
            continue
        r = have.get(ts)
        if r:
            r.hs, r.workers, r.reject = v, w, rj
        else:
            db.add(pm.PoolHourly(account_id=a.id, ts=ts, hs=v, workers=w, reject=rj))

    notes = []
    if with_profit:
        try:
            rows = cli.profit_days(key, coin, uid)
        except ViaBTCError as e:  # 3002: the link does not share accruals
            rows = []
            notes.append("начисления закрыты" if str(e).startswith("3002") else f"начисления: {e}")
        days = {}
        for r in rows:
            try:
                days[date.fromisoformat(r["date"])] = r
            except (KeyError, ValueError):
                continue
        have_d = {r.day: r for r in db.scalars(select(pm.PoolDaily).where(pm.PoolDaily.account_id == a.id, pm.PoolDaily.day.in_(list(days) or [date(1970, 1, 1)])))}
        for d, r in days.items():
            x = have_d.get(d) or pm.PoolDaily(account_id=a.id, day=d)
            x.hs, x.profit = num(r.get("hashrate")), num(r.get("total_profit"))
            x.pps, x.pplns = num(r.get("pps_profit")), num(r.get("pplns_profit"))
            x.fpps_rate, x.unit_output = num(r.get("pps_plus_rate")), num(r.get("unit_output"))
            x.price_usd, x.money_usd = num(r.get("price")), num(r.get("total_money"))
            if d not in have_d:
                db.add(x)

    if with_profit:  # daily chart (90 days) fills the days the archive and accruals do not cover
        try:
            dd = cli.daily(key, coin, uid)
        except ViaBTCError:
            dd = {}
        if dd:
            db.flush()
            dmap = {datetime.fromtimestamp(ts, tz=timezone.utc).date(): v for ts, v in dd.items()}
            have_dd = {r.day: r for r in db.scalars(select(pm.PoolDaily).where(pm.PoolDaily.account_id == a.id, pm.PoolDaily.day.in_(list(dmap))))}
            for day_, v in dmap.items():
                x = have_dd.get(day_)
                if x is None:
                    db.add(pm.PoolDaily(account_id=a.id, day=day_, hs=v))
                elif x.hs is None:
                    x.hs = v

    if with_workers:
        try:
            ws = cli.workers(key, coin, uid)
        except ViaBTCError as e:
            ws = []
            notes.append("воркеры закрыты" if str(e).startswith("3002") else f"воркеры: {e}")
        have_w = {w.name: w for w in db.scalars(select(pm.PoolWorker).where(pm.PoolWorker.account_id == a.id))}
        t = datetime.now(timezone.utc)
        for r in ws:
            name = str(r.get("name") or "")
            if not name:
                continue
            w = have_w.get(name)
            if not w:
                w = pm.PoolWorker(account_id=a.id, name=name)
                db.add(w)
                have_w[name] = w
            w.full_name = f"{(r.get('user') or a.account or '').lower()}.{name.lower()}"
            w.status = r.get("status")
            w.hs_10m, w.hs_1h, w.hs_1d = num(r.get("hashrate_10min")), num(r.get("hashrate_1hour")), num(r.get("hashrate_1day"))
            w.reject_1h, w.online_7d_h = num(r.get("reject_rate_1hour")), num(r.get("online_time_7d"))
            w.last_active, w.updated_at = _ts(r.get("last_active")), t
    a.last_ok = datetime.now(timezone.utc)
    a.last_error = None
    if notes:
        a.note = "; ".join(notes)


TRUSTPOOL_API = "https://trustpool.cc/res/saas/observer"
VIA_LIKE = ("viabtc", "trustpool")


def via_client(provider: str, cache: dict | None = None) -> ViaBTC:
    cache = cache if cache is not None else {}
    if provider not in cache:
        from app.services.pools import viabtc as _v  # noqa: PLC0415  (late lookup keeps tests able to swap the client)
        cache[provider] = _v.ViaBTC(api=TRUSTPOOL_API) if provider == "trustpool" else _v.ViaBTC()
    return cache[provider]


def apply_snap(db: Session, a: pm.PoolAccount, s) -> None:
    """Normalized snapshot of any pool → account, hourly archive, days, workers."""
    if s.account:
        a.account = str(s.account)[:128]
    a.hs_10m, a.hs_1h, a.hs_1d = s.hs_10m, s.hs_1h if s.hs_1h is not None else s.hs_10m, s.hs_1d
    a.workers_active, a.workers_inactive, a.workers_invalid = s.workers_active, s.workers_inactive, s.workers_invalid
    a.balance, a.profit_24h, a.profit_total = s.balance, s.profit_24h, s.profit_total
    now = datetime.now(timezone.utc).timestamp()
    hourly = {ts: v for ts, v in s.hourly.items() if ts + 3600 <= now and v[0] is not None}
    if hourly:
        have = {r.ts: r for r in db.scalars(select(pm.PoolHourly).where(pm.PoolHourly.account_id == a.id, pm.PoolHourly.ts.in_(list(hourly))))}
        for ts, (v, w, rj) in hourly.items():
            r = have.get(ts)
            if r:
                r.hs, r.workers, r.reject = v, w if w is not None else r.workers, rj
            else:
                db.add(pm.PoolHourly(account_id=a.id, ts=ts, hs=v, workers=w, reject=rj))
    if s.days:
        have_d = {r.day: r for r in db.scalars(select(pm.PoolDaily).where(pm.PoolDaily.account_id == a.id, pm.PoolDaily.day.in_(list(s.days))))}
        for d, v in s.days.items():
            x = have_d.get(d)
            if x is None:
                x = pm.PoolDaily(account_id=a.id, day=d)
                db.add(x)
            if v.get("hs") is not None:
                x.hs = v["hs"]
            if v.get("profit") is not None:
                x.profit = v["profit"]
    if s.workers is not None:
        have_w = {w.name: w for w in db.scalars(select(pm.PoolWorker).where(pm.PoolWorker.account_id == a.id))}
        t = datetime.now(timezone.utc)
        for r in s.workers:
            name = str(r.get("name") or "")[:128]
            if not name:
                continue
            w = have_w.get(name)
            if not w:
                w = pm.PoolWorker(account_id=a.id, name=name)
                db.add(w)
                have_w[name] = w
            w.full_name = str(r.get("full") or f"{a.account or ''}.{name}").lower()[:200]
            w.status = r.get("status")
            w.hs_10m, w.hs_1h, w.hs_1d = r.get("hs_10m"), r.get("hs_1h") if r.get("hs_1h") is not None else r.get("hs_10m"), r.get("hs_1d")
            w.reject_1h, w.online_7d_h = r.get("reject"), r.get("online_7d_h")
            w.last_active, w.updated_at = r.get("last_active"), t
    a.last_ok = datetime.now(timezone.utc)
    a.last_error = None
    a.note = "; ".join(s.notes) if s.notes else None


def sync_any(db: Session, a: pm.PoolAccount, with_workers: bool = True, with_profit: bool = True,
             via: dict | None = None, rds: dict | None = None) -> None:
    from app.services.pools.readers import reader  # noqa: PLC0415

    if a.provider in VIA_LIKE:
        sync_account(db, via_client(a.provider, via), a, with_workers, with_profit)
        return
    rds = rds if rds is not None else {}
    if a.provider not in rds:
        rds[a.provider] = reader(a.provider)
    rd = rds[a.provider]
    if rd is None:
        raise RuntimeError("этот пул пока читается только по ссылке в кабинете")
    s = rd.read(a.url, a.access_key, a.coin, with_workers, with_profit, account=a.account)
    apply_snap(db, a, s)


def relink(db: Session) -> int:
    """Links imported from the «Другие пулы» sheet (key = sha1(url), coin = "") → real key, coin and reading."""
    from app.services.pools.readers import PoolError, reader  # noqa: PLC0415
    from app.services.pools.viabtc import coin_from_link, parse_key  # noqa: PLC0415

    n = 0
    for a in db.scalars(select(pm.PoolAccount).where(pm.PoolAccount.coin == "", pm.PoolAccount.provider != "viabtc").order_by(pm.PoolAccount.id)).all():
        url = (a.url or "").strip()
        p = None
        if a.provider == "trustpool":
            k = parse_key(url)
            p = {"key": k, "coin": coin_from_link(url) or "BTC"} if k else None
        else:
            rd = reader(a.provider)
            p = rd.parse(url) if rd else None
        if not p or not p.get("key"):
            a.last_error = "в ссылке нет ключа наблюдателя — нужна ссылка Observer / Watcher из кабинета пула"
            a.coin = "?"
            n += 1
            continue
        coin = p.get("coin")
        if not coin:
            try:
                coin = (rd.read(url, p["key"], None, False, False).coin or "BTC").upper()
            except PoolError as e:
                coin, a.last_error = "BTC", str(e)
        dup = db.scalar(select(pm.PoolAccount).where(pm.PoolAccount.provider == a.provider, pm.PoolAccount.access_key == p["key"][:128],
                                                     pm.PoolAccount.coin == coin, pm.PoolAccount.id != a.id))
        if dup is not None:
            a.coin, a.active = "?", False
            a.last_error = f"та же ссылка уже есть (№{dup.id})"
            n += 1
            continue
        a.access_key, a.coin, a.active, a.track = p["key"][:128], coin, True, True
        if p.get("account") and not a.account:
            a.account = p["account"][:128]
        db.flush()
        n += 1
    db.commit()
    return n


def run_pools(db: Session, cli: ViaBTC | None = None, with_workers: bool = True, with_profit: bool = True,
              providers: tuple[str, ...] | None = None) -> tuple[int, int]:
    try:
        relink(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("relink failed")
    via = {"viabtc": cli} if cli else {}
    rds: dict = {}
    ok = bad = 0
    q = select(pm.PoolAccount).where(pm.PoolAccount.active.is_(True)).order_by(pm.PoolAccount.id)
    if providers:
        q = q.where(pm.PoolAccount.provider.in_(providers))
    for a in db.scalars(q).all():
        try:
            sync_any(db, a, with_workers, with_profit, via, rds)
            db.commit()
            ok += 1
        except Exception as e:  # one bad link must not stop the rest
            db.rollback()
            a = db.get(pm.PoolAccount, a.id)
            a.last_error = str(e)[:500]
            db.commit()
            bad += 1
    return ok, bad


def import_rows(db: Session, rows: list[dict], cli: ViaBTC | None = None, resolve: bool = True) -> dict:
    """rows: {key, coin, user_name, group, track, chat, thread, threshold}. Resolves sub-accounts via info()."""
    cli = cli or ViaBTC()
    added = updated = invalid = 0
    info_cache: dict[str, dict | None] = {}
    for r in rows:
        key, coin = r["key"], (r.get("coin") or "BTC").upper()
        uid, account = 0, r.get("user_name")
        if resolve:
            if key not in info_cache:
                try:
                    info_cache[key] = cli.info(key)
                except Exception:
                    info_cache[key] = None
            inf = info_cache[key]
            if inf is None:
                invalid += 1
                continue
            account = inf.get("account") or account
            subs = inf.get("accounts") or []
            want = (r.get("user_name") or "").lower()
            for s in subs:
                if str(s.get("account", "")).lower() == want:
                    uid, account = int(s.get("user_id") or 0), s.get("account")
        a = db.scalar(select(pm.PoolAccount).where(pm.PoolAccount.provider == "viabtc", pm.PoolAccount.access_key == key,
                                                   pm.PoolAccount.coin == coin, pm.PoolAccount.sub_user_id == uid))
        if a is None:
            a = pm.PoolAccount(provider="viabtc", access_key=key, coin=coin, sub_user_id=uid,
                               url=f"https://www.viabtc.com/observer/dashboard?access_key={key}&coin={coin}")
            db.add(a)
            added += 1
        else:
            updated += 1
        first = a.id is None or a.group_name is None or a.group_name == r.get("group")
        a.account = account
        if first:  # the same link may repeat on test sheets — the first sheet keeps it
            a.note_user = r.get("user_name")
            a.group_name = r.get("group")
        a.track = bool(r.get("track", True)) or bool(a.track and not first)
        a.tg_chat = str(r["chat"]) if r.get("chat") not in (None, "") else None
        a.tg_thread = int(r["thread"]) if r.get("thread") not in (None, "") else None
        a.threshold_ths = num(r.get("threshold"))
        a.active = True
        db.flush()
    db.commit()
    return {"added": added, "updated": updated, "invalid": invalid}


def rows_from_xlsx(data: bytes) -> tuple[list[dict], list[dict]]:
    """Mining Monitor table → (viabtc rows, other pool links)."""
    import io
    import re

    import openpyxl

    from app.services.pools.viabtc import parse_key

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out, other = [], []
    for ws in wb.worksheets:
        if ws.title.lower().startswith("инструкц"):
            continue
        grp = None
        for row in ws.iter_rows(values_only=True):
            vals = [x for x in row if x not in (None, "")]
            if not vals:
                continue
            if ws.title.lower().startswith("другие"):
                for v in vals:
                    s = str(v).strip()
                    if s.startswith("http"):
                        other.append({"group": grp, "url": s})
                    else:
                        grp = s
                continue
            if len(row) < 2 or not row[1]:
                continue
            key = parse_key(str(row[1]))
            if not key:
                continue
            coin = (str(row[2]).strip().upper() if len(row) > 2 and row[2] else None) or (re.search(r"coin=([A-Za-z]+)", str(row[1])) or [None, "BTC"])[1]
            out.append({
                "group": ws.title, "track": str(row[0]).strip().lower() in ("да", "yes", "1", "true"),
                "key": key, "coin": coin.upper(), "user_name": (str(row[3]).strip() if len(row) > 3 and row[3] else None),
                "chat": (str(int(row[4])) if len(row) > 4 and isinstance(row[4], (int, float)) else (row[4] if len(row) > 4 else None)),
                "thread": (int(row[5]) if len(row) > 5 and isinstance(row[5], (int, float)) else None),
                "threshold": (row[6] if len(row) > 6 else None),
            })
    return out, other


PROVIDERS = {"viabtc.com": "viabtc", "binance.com": "binance", "trustpool.ru": "trustpool", "trustpool.cc": "trustpool", "f2pool.com": "f2pool",
             "emcd.io": "emcd", "antpool.com": "antpool", "2miners.com": "2miners", "bitfufu.net": "bitfufu"}


def provider_of(url: str) -> str:
    host = (url.split("/")[2] if "//" in url else url).lower()
    for dom, name in PROVIDERS.items():
        if host.endswith(dom):
            return name
    return host[:32]


def import_other(db: Session, other: list[dict]) -> int:
    """Links to other pools are stored now; their readers come one by one."""
    import hashlib

    n = 0
    seen: set[str] = set()
    for o in other:
        url = o["url"].strip()
        prov = provider_of(url)
        key = hashlib.sha1(url.encode()).hexdigest()
        if prov == "viabtc" or key in seen:
            continue
        seen.add(key)
        a = db.scalar(select(pm.PoolAccount).where(pm.PoolAccount.provider == prov, pm.PoolAccount.access_key == key))
        if a is None:
            a = pm.PoolAccount(provider=prov, access_key=key, coin="", sub_user_id=0, url=url, active=False)
            db.add(a)
            n += 1
        a.group_name = a.group_name or o.get("group")
    db.commit()
    return n
