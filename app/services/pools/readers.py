"""Read-only observer links of other pools → one normalized snapshot.

Each reader: parse(url) → {key, coin, account?} | None; read(url, key, coin, with_workers, with_profit) → Snap.
Hashrate is H/s everywhere, reject is a fraction (0.0016 = 0.16 %), worker status is active / unactive / invalid.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.services.pools.viabtc import MULT, num

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


class PoolError(RuntimeError):
    """Readable reason shown to the person (last_error)."""


def hsu(v, unit: str | None = None) -> float | None:
    """'225.18 TH/s' / ('432.93', 'PH/s') / '2113.73 Thash/s' → H/s."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    m = re.fullmatch(r"([0-9.eE+-]+)\s*([A-Za-z/]*)", s)
    if not m:
        return None
    try:
        x = float(m.group(1))
    except ValueError:
        return None
    u = (unit if unit is not None else m.group(2)) or ""
    u = u.strip().upper().replace("HASH/S", "").replace("H/S", "").replace("H", "")
    return x * MULT.get(u[:1], 1.0)


def pct(v) -> float | None:
    """'0.16%' → 0.0016; 0.0102 → 0.0102."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    if s.endswith("%"):
        x = num(s[:-1])
        return x / 100 if x is not None else None
    return num(s)


def _dt(x, ms: bool = False) -> datetime | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not x:
        return None
    return datetime.fromtimestamp(x / 1000 if ms else x, tz=timezone.utc)


def hour_avg(points: list[tuple[int, float, float | None]]) -> dict[int, tuple[float, int | None, float | None]]:
    """(ts sec, hs, reject) of any step → hourly averages keyed by hour start."""
    acc: dict[int, list] = {}
    for ts, v, rj in points:
        if v is None:
            continue
        h = int(ts) // 3600 * 3600
        a = acc.setdefault(h, [0.0, 0, 0.0, 0])
        a[0] += v
        a[1] += 1
        if rj is not None:
            a[2] += rj
            a[3] += 1
    return {h: (a[0] / a[1], None, (a[2] / a[3]) if a[3] else None) for h, a in acc.items()}


@dataclass
class Snap:
    account: str | None = None
    coin: str | None = None
    hs_10m: float | None = None
    hs_1h: float | None = None
    hs_1d: float | None = None
    workers_active: int | None = None
    workers_inactive: int | None = None
    workers_invalid: int | None = None
    balance: float | None = None
    profit_24h: float | None = None
    profit_total: float | None = None
    hourly: dict = field(default_factory=dict)      # ts → (hs, workers, reject)
    days: dict = field(default_factory=dict)        # date → {hs, profit, kind}
    workers: list | None = None                     # [{name, full, status, hs_10m, hs_1h, hs_1d, reject, online_7d_h, last_active}]
    notes: list = field(default_factory=list)


class Base:
    name = ""
    hosts: tuple[str, ...] = ()

    def __init__(self, timeout: float = 20.0, pause: float = 0.2, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=timeout, follow_redirects=True,
                                         headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
        self.pause = pause

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None, timeout: float | None = None):
        try:
            r = self.http.get(url, params=params, headers=headers, **({"timeout": timeout} if timeout else {}))
        except httpx.HTTPError as e:
            raise PoolError(f"{self.name} не отвечает: {type(e).__name__}") from e
        if self.pause:
            time.sleep(self.pause)
        if r.status_code == 404:
            raise PoolError("ссылка не открывается — пул её отключил или по ней нет данных")
        if r.status_code >= 400:
            raise PoolError(f"{self.name} ответил {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise PoolError(f"{self.name} вернул не данные, а страницу") from e

    @staticmethod
    def q(url: str) -> dict:
        return {k.lower(): v[0].strip() for k, v in parse_qs(urlparse(url).query).items()}


# ---------------- Antpool ----------------
class Antpool(Base):
    name = "Antpool"
    API = "https://v3.antpool.com/auth/v3/observer/api/"

    def parse(self, url):
        q = self.q(url)
        if not q.get("accesskey") or not q.get("observeruserid"):
            return None
        return {"key": f"{q['accesskey']}:{q['observeruserid']}", "coin": (q.get("cointype") or "BTC").upper(),
                "account": q["observeruserid"]}

    def call(self, path, key, coin, **extra):
        ak, uid = key.split(":", 1)
        j = self._get(self.API + path, {"accessKey": ak, "coinType": coin, "observerUserId": uid, **extra})
        if str(j.get("code")) != "000000":
            m = str(j.get("msg") or j.get("code"))
            raise PoolError("Antpool: наблюдатель удалён или ссылка отозвана" if "NOT_EXIST" in m else f"Antpool: {m}")
        return j.get("data") or {}

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        h = self.call("hash/query", key, coin)
        s = Snap(account=h.get("userId") or key.split(":", 1)[1], coin=coin,
                 hs_10m=hsu(h.get("hsNow"), h.get("hsNowUnit")), hs_1d=hsu(h.get("hsLast1D"), h.get("hsLast1DUnit")),
                 workers_active=h.get("onlineWorkerNum"), workers_inactive=h.get("offlineWorkerNum"),
                 workers_invalid=h.get("disableWorkerNum"))
        sp = self.call("user/speed", key, coin, type="hour").get("graphContractPointList") or []
        for p in sp:
            ts = int(p.get("timestamp") or 0) // 1000
            if ts:
                s.hourly[ts // 3600 * 3600] = (hsu(p.get("hashRate"), p.get("hashRateUnit")), None, pct(p.get("rejectRatio")))
        if sp:
            s.hs_1h = s.hourly[max(s.hourly)][0]
        try:
            e = self.call("earnings/query", key, coin)
            s.balance, s.profit_24h, s.profit_total = num(e.get("earningsNoPay")), num(e.get("earningsYesterday")), num(e.get("earningsTotal"))
            st = _dt(e.get("startTime"), ms=True)
            if st and s.profit_24h is not None:
                s.days[st.date()] = {"profit": s.profit_24h, "hs": hsu(e.get("effectiveHash"), e.get("effectiveHashUnit"))}
        except PoolError as ex:
            s.notes.append(f"начисления: {ex}")
        if with_profit:
            try:
                for p in self.call("user/speed", key, coin, type="day").get("graphContractPointList") or []:
                    d = _dt(p.get("timestamp"), ms=True)
                    if d:
                        s.days.setdefault(d.date(), {})["hs"] = s.days.get(d.date(), {}).get("hs") or hsu(p.get("hashRate"), p.get("hashRateUnit"))
            except PoolError:
                pass
        if with_workers:
            ws = []
            for page in range(1, 41):
                d = self.call("worker/list", key, coin, search="", workerStatus=0, pageNum=page, pageSize=100)
                items = d.get("items") or []
                for w in items:
                    st = int(w.get("workerStatus") or 0)
                    ws.append({"name": w.get("workerId"), "full": w.get("userWorkerId"),
                               "status": "active" if st == 1 else ("invalid" if st == 2 else "unactive"),
                               "hs_10m": hsu(w.get("hsLast10Min")), "hs_1h": hsu(w.get("hsLast1Hour") or w.get("hsLast1H")),
                               "hs_1d": hsu(w.get("hsLast1D")), "reject": pct(w.get("rejectRatio")),
                               "last_active": _dt(w.get("shareLastTime"), ms=True)})
                if len(items) < 100:
                    break
            s.workers = ws
        return s


# ---------------- F2Pool ----------------
class F2Pool(Base):
    name = "F2Pool"
    XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}
    COLS = ["name", "hashrate", "hashrate_last_day", "stalerate_last_day", "last_share"]

    def parse(self, url):
        u = urlparse(url)
        m = re.match(r"/mining-user(?:-([a-z0-9]+))?/([0-9a-f]{16,64})", u.path, re.I)
        if not m:
            return None
        return {"key": m.group(2).lower(), "coin": (m.group(1) or "btc").upper(), "account": self.q(url).get("user_name")}

    def base(self, coin, key):
        return f"https://www.f2pool.com/mining-user{'' if coin == 'BTC' else '-' + coin.lower()}/{key}"

    def account_of(self, coin, key) -> str | None:
        try:
            r = self.http.get(self.base(coin, key), headers={"Accept": "text/html,application/xhtml+xml"}, timeout=40)
        except httpx.HTTPError:
            return None
        if r.status_code == 404:
            raise PoolError("ссылка не открывается — пул её отключил")
        m = re.search(r"<title>\s*([\w.-]+)\s*-\s*F2Pool", r.text)
        return m.group(1) if m else None

    def x(self, coin, key, **params):
        j = self._get(self.base(coin, key), params, self.XHR, timeout=45)
        if isinstance(j, dict) and j.get("status") not in (None, "ok"):
            raise PoolError(f"F2Pool: {j.get('msg') or j.get('status')}")
        return j

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        if not re.fullmatch(r"[0-9a-f]{16,64}", key or ""):
            raise PoolError("старый формат ссылки F2Pool — создайте ссылку наблюдателя заново (Настройки → Наблюдатель)")
        acc = account or (self.parse(url or "") or {}).get("account") or self.account_of(coin, key)
        if not acc:
            raise PoolError("F2Pool не показывает имя аккаунта по ссылке")
        s = Snap(account=acc, coin=coin)
        d = (self.x(coin, key, user_name=acc, action="load_by_duration", duration=1).get("data") or {})
        hh = d.get("hashrate_history") or {}
        mu = re.search(r"\(([A-Za-z]+)/S\)", str(hh.get("key") or ""), re.I)
        mult = MULT.get((mu.group(1) if mu else "T")[:1].upper(), 1e12)
        rj = {int(p[0]): num(p[1]) for p in (d.get("hashrate_history_stale_rate") or {}).get("values") or [] if p}
        pts = [(int(p[0]) // 1000, (num(p[1]) or 0) * mult, (rj.get(int(p[0])) or 0) / 100) for p in hh.get("values") or [] if p]
        s.hourly = hour_avg(pts)
        if pts:
            s.hs_10m = pts[-1][1]
            last = [v for t, v, _ in pts if t >= pts[-1][0] - 3600]
            s.hs_1h = sum(last) / len(last)
        if with_workers:
            ws, start = [], 0
            while True:
                p = {"user_name": acc, "action": "get_pagination_workers", "account": acc, "currency": coin, "tab": "all",
                     "hashrate_type": "real_time", "draw": 1, "start": start, "length": 500, "tagOnly": 0}
                for i, c in enumerate(self.COLS):
                    p[f"columns[{i}][data]"] = c
                j = self.x(coin, key, **p)
                od = j.get("originData") or {}
                if start == 0:
                    sm = od.get("summary") or {}
                    s.hs_1d = num(sm.get("hash_rate_daily"))
                    s.workers_active, s.workers_inactive = od.get("worker_length_online"), od.get("worker_length_offline")
                for w in j.get("data") or []:
                    day = num(w.get("hashes_last_day"))
                    disp_d = num(w.get("hashrate_last_day"))
                    wm = (day / 86400 / disp_d) if day and disp_d else mult
                    st = int(w.get("status") or 0)
                    ws.append({"name": w.get("name"), "full": f"{acc}.{w.get('name')}",
                               "status": "active" if st == 0 else ("invalid" if st == 2 else "unactive"),
                               "hs_10m": (num(w.get("hashrate")) or 0) * wm, "hs_1d": (day / 86400) if day is not None else None,
                               "reject": (num(w.get("stalerate_last_day")) or 0) / 100, "online_7d_h": num(w.get("online_time_7d")),
                               "last_active": _dt(w.get("last_share"))})
                start += 500
                if start >= int(j.get("recordsTotal") or 0) or start >= 20000:
                    break
            s.workers = ws
        if with_profit:
            try:
                inc = (self.x(coin, key, user_name=acc, currency_code=coin.lower(), account=acc,
                              action="load_payout_history_income").get("data") or {}).get("income_data") or []
                for r in inc:
                    if str(r.get("currency_code") or coin).lower() != coin.lower():
                        continue
                    dd = _dt(r.get("created_at"))
                    if not dd:
                        continue
                    day = (dd - timedelta(days=1)).date()  # accrual of the previous UTC day
                    x = s.days.setdefault(day, {"profit": 0.0})
                    x["profit"] = (x.get("profit") or 0) + (num(r.get("amount")) or 0)
                    x["hs"] = hsu(r.get("hash_rate"))
                if inc:
                    s.profit_24h = s.days.get(max(s.days), {}).get("profit")
            except PoolError as ex:
                s.notes.append(f"начисления: {ex}")
        return s


# ---------------- EMCD ----------------
class EMCD(Base):
    name = "EMCD"
    API = "https://api.emcd.io/v1/watcher/"
    MERGED = {"doge": "ltc", "fb": "btc"}

    def parse(self, url):
        m = re.search(r"/watcher/(?:[a-z]{2}/)?([0-9a-f-]{36})", unquote(url).strip(), re.I)
        return {"key": m.group(1).lower(), "coin": None} if m else None

    def pick_coin(self, coins: dict) -> str:
        best = None
        for c, v in coins.items():
            if c in self.MERGED:
                continue
            r = num(v.get("total_reward")) or 0
            if best is None or r > best[1]:
                best = (c, r)
        return (best[0] if best and best[1] else "btc").upper()

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        b = f"{self.API}{key}/"
        inf = self._get(b + "info")
        coins = inf.get("coins") or {}
        coin = (coin or self.pick_coin(coins)).upper()
        c = coin.lower()
        cv = coins.get(c) or {}
        s = Snap(account=inf.get("username"), coin=coin, balance=num(cv.get("balance")), profit_total=num(cv.get("total_reward")))
        w = self._get(b + f"{c}/workers")
        th, tc = w.get("total_hashrate") or {}, w.get("total_count") or {}
        s.hs_10m, s.hs_1h, s.hs_1d = num(th.get("hashrate")), num(th.get("hashrate1h")), num(th.get("hashrate24h"))
        s.workers_active, s.workers_inactive, s.workers_invalid = tc.get("active"), tc.get("inactive"), tc.get("dead_count")
        hr = ((self._get(b + f"{c}/hashrate").get("data") or {}).get("hrs") or {})
        pts = [(int(t), num(v) or 0, None) for t, v in ((hr.get("week") or {}).get("tsHrs") or {}).items()]
        pts += [(int(t), num(v) or 0, None) for t, v in ((hr.get("day") or {}).get("tsHrs") or {}).items()]
        s.hourly = hour_avg(sorted(pts))
        if with_workers:
            s.workers = [{"name": x.get("worker"), "full": f"{x.get('user') or s.account}.{x.get('worker')}",
                          "status": "active" if int(x.get("status") or 0) == 1 else "unactive",
                          "hs_10m": num(x.get("hashrate")), "hs_1h": num(x.get("hashrate1h")), "hs_1d": num(x.get("hashrate24h")),
                          "reject": pct(x.get("reject")), "last_active": _dt(x.get("last_active"))}
                         for x in w.get("details") or [] if x.get("worker")]
        if with_profit:
            try:
                for r in self._get(b + f"{c}/income").get("income") or []:
                    d = _dt(r.get("timestamp"))
                    if d and r.get("type", "mining") == "mining":
                        day = (d - timedelta(days=1)).date()
                        x = s.days.setdefault(day, {"profit": 0.0})
                        x["profit"] = (x.get("profit") or 0) + (num(r.get("income")) or 0)
                        x["hs"] = num(r.get("total_hashrate"))
                if s.days:
                    s.profit_24h = s.days[max(s.days)].get("profit")
            except PoolError as ex:
                s.notes.append(f"начисления: {ex}")
        return s


# ---------------- Binance Pool ----------------
class Binance(Base):
    name = "Binance Pool"
    API = "https://pool.binance.com/mining-api/v1/public/pool/"

    def parse(self, url):
        t = self.q(url).get("urlparams") or self.q(url).get("observertoken")
        return {"key": t, "coin": None} if t else None

    def call(self, path, key, **extra):
        j = self._get(self.API + path, {"observerToken": key, **extra})
        if j.get("code") not in (0, "0", None) or not j.get("success", True):
            raise PoolError(f"Binance: {j.get('msg') or j.get('code')}")
        return j.get("data") or {}

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        a = self.call("authorize", key)
        coin = (coin or ((a.get("coinName") or {}).get("name")) or "BTC").upper()
        s = Snap(account=(a.get("userHash") or {}).get("userName"), coin=coin)
        st = self.call("stat", key)
        s.hs_10m, s.hs_1d = hsu(st.get("h15m")), hsu(st.get("h1d"))
        s.workers_active, s.workers_inactive = st.get("validNum"), st.get("invalidNum")
        pt, py = st.get("profitToday") or {}, st.get("profitYesterday") or {}
        s.profit_24h = num(py.get(coin)) if py else None
        for h in st.get("hoursList") or []:
            ts = int(h.get("time") or 0) // 1000
            if ts:
                s.hourly[ts // 3600 * 3600] = (hsu(h.get("hash")), h.get("validNum"), pct(h.get("reject")))
        if s.hourly:
            s.hs_1h = s.hourly[max(s.hourly)][0]
        for d in st.get("dayList") or []:
            dd = _dt(d.get("time"), ms=True)
            if dd:
                s.days[dd.date()] = {"hs": num(d.get("total")) or hsu(d.get("hash"))}
        if pt.get(coin) is not None:
            s.notes.append(f"сегодня начислено {pt.get(coin)} {coin}")
        if with_workers:
            ws = []
            for page in range(1, 101):
                m = self.call("miner/index", key, pageIndex=page, sort=0, sortColumn=1, workerStatus=0)
                items = m.get("workerDatas") or []
                for w in items:
                    stt = int(w.get("status") or 0)
                    ws.append({"name": w.get("workerName"), "full": f"{s.account}.{w.get('workerName')}",
                               "status": "active" if stt == 1 else ("invalid" if stt == 3 else "unactive"),
                               "hs_10m": hsu(w.get("h15m")), "hs_1d": hsu(w.get("h1d")), "reject": pct(w.get("rejectRate")),
                               "last_active": _dt(w.get("lastShareTime"), ms=True)})
                if len(items) < int(m.get("pageSize") or 20) or len(ws) >= int(m.get("totalNum") or 0):
                    break
            s.workers = ws
        return s


# ---------------- 2Miners ----------------
class TwoMiners(Base):
    name = "2Miners"

    def parse(self, url):
        u = urlparse(url)
        sub = (u.hostname or "").split(".")[0].lower()
        m = re.search(r"/account/([^/?#]+)", u.path)
        if not m or sub in ("www", "2miners"):
            return None
        return {"key": unquote(m.group(1)), "coin": sub.upper()}

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        j = self._get(f"https://{coin.lower()}.2miners.com/api/accounts/{key}")
        s = Snap(account=key[:24] + "…" if len(key) > 26 else key, coin=coin,
                 hs_10m=num(j.get("currentHashrate")), hs_1d=num(j.get("hashrate")),
                 workers_active=j.get("workersOnline"), workers_inactive=j.get("workersOffline"))
        st = j.get("stats") or {}
        s.balance = (num(st.get("balance")) or 0) / 1e8 if st.get("balance") is not None else None
        s.profit_24h = (num(j.get("24hreward")) or 0) / 1e8 if j.get("24hreward") is not None else None
        s.hourly = hour_avg([(int(p.get("x") or 0), num(p.get("minerHash")) or 0, None) for p in j.get("minerCharts") or []])
        if with_workers:
            s.workers = [{"name": n, "full": n, "status": "unactive" if w.get("offline") else "active",
                          "hs_10m": num(w.get("hr")), "hs_1d": num(w.get("hr2")), "last_active": _dt(w.get("lastBeat"))}
                         for n, w in (j.get("workers") or {}).items()]
        return s


# ---------------- BitFuFu ----------------
class BitFuFu(Base):
    name = "BitFuFu"
    API = "https://us-miner-service.bitfufu.net/v1/miner/"
    TZ = timezone(timedelta(hours=8))  # report_time is Beijing time

    def parse(self, url):
        q = self.q(url)
        if not q.get("access_key") or not q.get("miner_prefix"):
            return None
        return {"key": f"{q['access_key']}:{q['miner_prefix']}", "coin": "BTC", "account": q["miner_prefix"]}

    def call(self, path, key, **extra):
        ak, pref = key.split(":", 1)
        j = self._get(self.API + path, {"access_key": ak, "miner_prefix": pref, **extra})
        if j.get("code") not in (0, "0"):
            raise PoolError(f"BitFuFu: {j.get('msg') or j.get('code')}")
        return j

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        d = self.call("prefix_stat", key).get("data") or {}
        s = Snap(account=key.split(":", 1)[1], coin=coin or "BTC",
                 hs_10m=hsu(d.get("hash_rate_minute"), d.get("hash_rate_unit_minute") or d.get("hash_rate_unit")),
                 hs_1h=hsu(d.get("hash_rate_hour"), d.get("hash_rate_unit_hour") or d.get("hash_rate_unit")),
                 hs_1d=hsu(d.get("hash_rate_day"), d.get("hash_rate_unit_day") or d.get("hash_rate_unit")))
        pts = []
        for p in self.call("prefix_share", key, dimension="hour").get("data") or []:
            try:
                t = datetime.strptime(p["report_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.TZ)
            except (KeyError, ValueError):
                continue
            pts.append((int(t.timestamp()), hsu(p.get("hash_rate"), p.get("hash_rate_unit")) or 0, pct(p.get("reject_rate"))))
        s.hourly = hour_avg(pts)
        if with_workers:
            ws = []
            for page in range(1, 51):
                j = self.call("list", key, page=page, size=100)
                items = j.get("data") or []
                for w in items:
                    st = int(w.get("status") or 0)
                    u = w.get("hash_rate_unit")
                    ws.append({"name": w.get("miner_name") or w.get("name"), "full": w.get("miner_name") or w.get("name"),
                               "status": "active" if st == 1 else "unactive",
                               "hs_10m": hsu(w.get("hash_rate_minute"), u), "hs_1h": hsu(w.get("hash_rate_hour"), u),
                               "hs_1d": hsu(w.get("hash_rate_day"), u), "reject": pct(w.get("reject_rate"))})
                if len(items) < 100 or len(ws) >= int(j.get("count") or 0):
                    break
            s.workers = [w for w in ws if w["name"]]
            s.workers_active = sum(1 for w in s.workers if w["status"] == "active")
            s.workers_inactive = len(s.workers) - s.workers_active
        return s


# ---------------- NiceHash (external address API) ----------------
class NiceHash(Base):
    name = "NiceHash"
    API = "https://api2.nicehash.com/main/api/v2/mining/external/"

    def parse(self, url):
        m = re.search(r"\b(bc1[0-9a-z]{20,80}|[13][1-9A-HJ-NP-Za-km-z]{25,34}|NHb[1-9A-HJ-NP-Za-km-z]{20,})\b", url)
        return {"key": m.group(1), "coin": "BTC"} if m else None

    def read(self, url, key, coin, with_workers=True, with_profit=True, account=None) -> Snap:
        j = self._get(f"{self.API}{key}/rigs2")
        s = Snap(account=key[:10] + "…", coin="BTC", balance=num(j.get("externalBalance")),
                 profit_24h=num(j.get("totalProfitability")))
        rigs = j.get("miningRigs") or []
        s.workers_active = sum(1 for r in rigs if r.get("minerStatus") == "MINING")
        s.workers_inactive = len(rigs) - s.workers_active
        if with_workers:
            s.workers = [{"name": r.get("name") or r.get("rigId"), "full": r.get("name") or r.get("rigId"),
                          "status": "active" if r.get("minerStatus") == "MINING" else "unactive",
                          "last_active": _dt(r.get("statusTime"), ms=True)} for r in rigs]
        s.notes.append("NiceHash отдаёт по адресу только риги и доходность, хешрейт — в кабинете")
        return s


READERS: dict[str, type[Base]] = {"antpool": Antpool, "f2pool": F2Pool, "emcd": EMCD, "binance": Binance,
                                  "2miners": TwoMiners, "bitfufu": BitFuFu, "nicehash": NiceHash}


def reader(provider: str, **kw) -> Base | None:
    c = READERS.get(provider)
    return c(**kw) if c else None


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
