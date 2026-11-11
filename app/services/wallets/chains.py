"""Сети кошельков: распознавание адреса и чтение входящих переводов из бесплатных публичных источников."""
from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Kirka"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ChainError(RuntimeError):
    pass


# ---------- адреса ----------
def b58decode(s: str) -> bytes | None:
    n = 0
    for ch in s:
        i = B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58check(s: str) -> bytes | None:
    d = b58decode(s)
    if not d or len(d) < 5:
        return None
    body, chk = d[:-4], d[-4:]
    return body if hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] == chk else None


def tron_from_hex(h: str) -> str:
    """41… hex → T… base58check."""
    b = bytes.fromhex(h[2:] if h.startswith("0x") else h)
    if len(b) == 20:
        b = b"\x41" + b
    return b58encode(b + hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4])


def _crc16(data: bytes) -> bytes:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc.to_bytes(2, "big")


def ton_raw(addr: str) -> str | None:
    """EQ…/UQ… или 0:hex → '0:HEX' (верхний регистр)."""
    a = addr.strip()
    m = re.fullmatch(r"(-?\d+):([0-9a-fA-F]{64})", a)
    if m:
        return f"{int(m.group(1))}:{m.group(2).upper()}"
    try:
        b = base64.urlsafe_b64decode(a.replace("+", "-").replace("/", "_") + "==")
    except (ValueError, TypeError):
        return None
    if len(b) != 36 or _crc16(b[:34]) != b[34:]:
        return None
    wc = b[1] if b[1] < 128 else b[1] - 256
    return f"{wc}:{b[2:34].hex().upper()}"


def ton_friendly(raw: str, bounceable: bool = False) -> str:
    wc, h = raw.split(":")
    body = bytes([0x11 if bounceable else 0x51, int(wc) & 0xFF]) + bytes.fromhex(h)
    return base64.urlsafe_b64encode(body + _crc16(body)).decode()


CHAINS = {
    "BTC": {"name": "Bitcoin", "net": "BTC", "coin": "BTC", "dec": 8, "ex": "mempool.space",
            "tx": "https://mempool.space/tx/{}", "addr": "https://mempool.space/address/{}"},
    "LTC": {"name": "Litecoin", "net": "LTC", "coin": "LTC", "dec": 8, "ex": "litecoinspace.org",
            "tx": "https://litecoinspace.org/tx/{}", "addr": "https://litecoinspace.org/address/{}"},
    "DOGE": {"name": "Dogecoin", "net": "DOGE", "coin": "DOGE", "dec": 8, "ex": "Blockchair",
             "tx": "https://blockchair.com/dogecoin/transaction/{}", "addr": "https://blockchair.com/dogecoin/address/{}"},
    "TRON": {"name": "TRON", "net": "TRC-20", "coin": "TRX", "dec": 6, "ex": "Tronscan",
             "tx": "https://tronscan.org/#/transaction/{}", "addr": "https://tronscan.org/#/address/{}"},
    "TON": {"name": "TON", "net": "TON", "coin": "TON", "dec": 9, "ex": "Tonviewer",
            "tx": "https://tonviewer.com/transaction/{}", "addr": "https://tonviewer.com/{}"},
    "ETH": {"name": "Ethereum", "net": "ERC-20", "coin": "ETH", "dec": 18, "ex": "Etherscan",
            "tx": "https://etherscan.io/tx/{}", "addr": "https://etherscan.io/address/{}"},
    "BSC": {"name": "BNB Chain", "net": "BEP-20", "coin": "BNB", "dec": 18, "ex": "BscScan",
            "tx": "https://bscscan.com/tx/{}", "addr": "https://bscscan.com/address/{}"},
    "SOL": {"name": "Solana", "net": "SOL", "coin": "SOL", "dec": 9, "ex": "Solscan",
            "tx": "https://solscan.io/tx/{}", "addr": "https://solscan.io/account/{}"},
}
ORDER = ["TRON", "BTC", "ETH", "BSC", "TON", "LTC", "SOL", "DOGE"]

# только настоящие контракты стейблкоинов; всё остальное с тем же названием — подделка
TOKENS = {
    "TRON": {"TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": ("USDT", 6), "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8": ("USDC", 6)},
    "ETH": {"0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6), "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6)},
    "BSC": {"0x55d398326f99059ff775485246999027b3197955": ("USDT", 18), "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": ("USDC", 18)},
    "TON": {"0:B113A994B5024A16719F69139328EB759596C38A25F59028B146FECDC3621DFE": ("USDT", 6)},
    "SOL": {"Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6), "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6)},
}
STABLE = {"USDT", "USDC"}


def detect(addr: str) -> list[str]:
    """Адрес → подходящие сети (у 0x-адреса две: Ethereum и BNB Chain)."""
    a = (addr or "").strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", a):
        return ["ETH", "BSC"]
    if re.fullmatch(r"bc1[ac-hj-np-z02-9]{11,71}", a.lower()) and a in (a.lower(), a.upper()):
        return ["BTC"]
    if re.fullmatch(r"ltc1[ac-hj-np-z02-9]{11,71}", a.lower()) and a in (a.lower(), a.upper()):
        return ["LTC"]
    if re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", a):
        b = b58check(a)
        return ["TRON"] if b and b[0] == 0x41 else []
    if re.fullmatch(r"[13LMD9A][1-9A-HJ-NP-Za-km-z]{25,34}", a):
        b = b58check(a)
        if b and len(b) == 21:
            return {0x00: ["BTC"], 0x05: ["BTC"], 0x30: ["LTC"], 0x32: ["LTC"], 0x1E: ["DOGE"], 0x16: ["DOGE"]}.get(b[0], [])
    if re.fullmatch(r"[EUk0]Q[A-Za-z0-9_-]{46}", a) or re.fullmatch(r"-?\d+:[0-9a-fA-F]{64}", a):
        return ["TON"] if ton_raw(a) else []
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", a):
        d = b58decode(a)
        return ["SOL"] if d and len(d) == 32 else []
    return []


ADDR_RE = re.compile(r"\b(0x[0-9a-fA-F]{40}|bc1[0-9a-zA-Z]{11,71}|ltc1[0-9a-zA-Z]{11,71}|[EUk0]Q[A-Za-z0-9_-]{46}"
                     r"|T[1-9A-HJ-NP-Za-km-z]{33}|[13LMD][1-9A-HJ-NP-Za-km-z]{25,34}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")


def find_address(text: str) -> tuple[str, list[str]] | None:
    if not text or "://" in text:
        return None
    for m in ADDR_RE.finditer(text):
        ch = detect(m.group(1))
        if ch:
            return m.group(1), ch
    return None


def norm(chain: str, addr: str) -> str:
    a = addr.strip()
    if chain in ("ETH", "BSC"):
        return a.lower()
    if chain in ("BTC", "LTC") and a.lower().startswith(("bc1", "ltc1")):
        return a.lower()
    if chain == "TON":
        r = ton_raw(a)
        return ton_friendly(r) if r else a
    return a


def short(a: str | None) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 14 else (a or "—")


# ---------- переводы ----------
@dataclass
class Transfer:
    uid: str
    txid: str
    token: str
    amount: float
    frm: str | None
    at: datetime
    flag: str | None = None   # spam | fake (подделка токена) | None
    dir: str = "in"           # in | out
    to: str | None = None     # куда ушло (для исходящих)


def _ts(x) -> datetime:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(x / 1000 if x > 1e11 else x, tz=timezone.utc)


# Хост, который ответил «лимит запросов», отдыхает: до этого времени к нему не ходим (общая для всех читателей).
_COOL: dict[str, float] = {}
_LAST: dict[str, float] = {}
GAP = {"api.trongrid.io": 0.4, "api.blockchair.com": 1.2, "api.mainnet-beta.solana.com": 1.0, "toncenter.com": 1.1,
       "api.blockcypher.com": 1.0}


class Reader:
    def __init__(self, http: httpx.Client | None = None, pause: float = 0.35, retries: int = 2):
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True,
                                         headers={"User-Agent": UA, "Accept": "application/json"})
        self.pause = pause
        self.retries = retries
        self.px: dict[str, float] = {}  # цены токенов, которые источник отдал вместе с переводом
        self._seen: dict[tuple, list] = {}
        import os  # noqa: PLC0415
        self.tron_key = os.environ.get("TRONGRID_API_KEY") or ""

    def _j(self, method: str, url: str, **kw):
        host = httpx.URL(url).host
        now = time.time()
        if _COOL.get(host, 0) > now:
            raise ChainError("источник просит подождать (лимит запросов)")
        if host == "api.trongrid.io" and self.tron_key:
            kw["headers"] = {**(kw.get("headers") or {}), "TRON-PRO-API-KEY": self.tron_key}
        last = None
        for i in range(self.retries + 1):
            gap = GAP.get(host, 0) if self.pause else 0
            wait = _LAST.get(host, 0) + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            _LAST[host] = time.time()
            try:
                r = self.http.request(method, url, **kw)
            except httpx.HTTPError as e:
                last = ChainError(f"сеть не ответила: {type(e).__name__}")
                if self.pause and i < self.retries:
                    time.sleep(1.5 * (i + 1))
                continue
            if self.pause:
                time.sleep(self.pause)
            if r.status_code == 429 or r.status_code in (502, 503, 504):
                last = ChainError("источник просит подождать (лимит запросов)" if r.status_code == 429 else f"источник ответил {r.status_code}")
                if i < self.retries:
                    if self.pause:
                        time.sleep(2.0 * (i + 1))
                    continue
                if r.status_code == 429:
                    _COOL[host] = time.time() + 120
                raise last
            if r.status_code >= 400:
                raise ChainError(f"источник ответил {r.status_code}")
            try:
                return r.json()
            except ValueError as e:
                raise ChainError("источник вернул не данные") from e
        raise last or ChainError("сеть не ответила")

    def get(self, url, **params):
        return self._j("GET", url, params=params or None)

    def rpc(self, url, method, params):
        j = self._j("POST", url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if j.get("error"):
            raise ChainError(str(j["error"].get("message") or j["error"])[:120])
        return j.get("result")

    # --- BTC / LTC (esplora) ---
    def esplora(self, base: str, chain: str, addr: str) -> list[Transfer]:
        out = []
        for tx in self.get(f"{base}/address/{addr}/txs") or []:
            st = tx.get("status") or {}
            if not st.get("confirmed"):
                continue
            ins = [(v.get("prevout") or {}).get("scriptpubkey_address") for v in tx.get("vin") or []]
            vout = tx.get("vout") or []
            if addr in ins:  # исходящий: сумма, ушедшая на чужие адреса (сдача себе не считается)
                val = sum(o.get("value") or 0 for o in vout if o.get("scriptpubkey_address") not in (addr, None))
                to = next((o.get("scriptpubkey_address") for o in vout if o.get("scriptpubkey_address") not in (addr, None)), None)
                if val > 0:
                    out.append(Transfer(f"{tx['txid']}:out", tx["txid"], chain, val / 1e8, addr, _ts(st.get("block_time")), dir="out", to=to))
                continue
            val = sum(o.get("value") or 0 for o in vout if o.get("scriptpubkey_address") == addr)
            if val <= 0:
                continue
            out.append(Transfer(tx["txid"], tx["txid"], chain, val / 1e8, next((x for x in ins if x), None), _ts(st.get("block_time")), to=addr))
        return out

    def btc(self, addr, w=None):
        return self.esplora("https://mempool.space/api", "BTC", addr)

    def ltc(self, addr, w=None):
        return self.esplora("https://litecoinspace.org/api", "LTC", addr)

    def doge(self, addr, w=None):
        try:
            return self._doge_blockchair(addr)
        except ChainError:
            return self._doge_blockcypher(addr)  # запасной источник

    def doge_to(self, txid: str, addr: str) -> str | None:
        """Куда ушли DOGE: первый выход перевода не на наш адрес."""
        d = self.get(f"https://api.blockcypher.com/v1/doge/main/txs/{txid}")
        for o in d.get("outputs") or []:
            a = (o.get("addresses") or [None])[0]
            if a and a != addr:
                return a
        return None

    def _doge_blockchair(self, addr):
        d = self.get(f"https://api.blockchair.com/dogecoin/dashboards/address/{addr}", transaction_details="true", limit=25)
        rows = ((d.get("data") or {}).get(addr) or {}).get("transactions") or []
        out = []
        for t in rows:
            ch = t.get("balance_change") or 0
            if ch and (t.get("block_id") or 0) > 0:
                at = datetime.strptime(t["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if t.get("time") else _ts(0)
                if ch > 0:
                    out.append(Transfer(t["hash"], t["hash"], "DOGE", ch / 1e8, None, at, to=addr))
                else:
                    out.append(Transfer(f"{t['hash']}:out", t["hash"], "DOGE", -ch / 1e8, addr, at, dir="out"))
        return out

    def _doge_blockcypher(self, addr):
        d = self.get(f"https://api.blockcypher.com/v1/doge/main/addrs/{addr}", limit=50)
        net: dict[str, list] = {}
        for t in d.get("txrefs") or []:
            if (t.get("confirmations") or 0) < 1:
                continue
            v = int(t.get("value") or 0) * (1 if (t.get("tx_input_n", -1) < 0) else -1)
            e = net.setdefault(t["tx_hash"], [0, t.get("confirmed")])
            e[0] += v
        out = []
        for h, (v, at) in net.items():
            when = datetime.fromisoformat(at.replace("Z", "+00:00")) if at else _ts(0)
            if v > 0:
                out.append(Transfer(h, h, "DOGE", v / 1e8, None, when, to=addr))
            elif v < 0:
                out.append(Transfer(f"{h}:out", h, "DOGE", -v / 1e8, addr, when, dir="out"))
        return out

    # --- TRON ---
    def tron(self, addr, w=None):
        out = []
        b = f"https://api.trongrid.io/v1/accounts/{addr}"
        for t in self.get(f"{b}/transactions/trc20", limit=50, only_confirmed="true").get("data") or []:
            if t.get("type") not in ("Transfer", None) or addr not in (t.get("to"), t.get("from")):
                continue
            ti = t.get("token_info") or {}
            real = TOKENS["TRON"].get(ti.get("address") or "")
            sym = real[0] if real else (str(ti.get("symbol") or "?")[:16])
            dec = real[1] if real else int(ti.get("decimals") or 6)
            amt = int(t.get("value") or 0) / 10 ** dec
            flag = None if real else ("fake" if sym.upper() in STABLE else "spam")
            out_ = t.get("from") == addr and t.get("to") != addr
            out.append(Transfer(f"{t['transaction_id']}:{ti.get('address')}" + (":out" if out_ else ""), t["transaction_id"], sym, amt,
                                t.get("from"), _ts(t.get("block_timestamp")), flag, "out" if out_ else "in", t.get("to")))
        for t in self.get(f"{b}/transactions", limit=30, only_confirmed="true").get("data") or []:
            c = ((t.get("raw_data") or {}).get("contract") or [{}])[0]
            if c.get("type") != "TransferContract" or (t.get("ret") or [{}])[0].get("contractRet") != "SUCCESS":
                continue
            v = (c.get("parameter") or {}).get("value") or {}
            try:
                to, frm = tron_from_hex(v.get("to_address") or ""), tron_from_hex(v.get("owner_address") or "")
            except ValueError:
                continue
            if to == addr and frm != addr:
                out.append(Transfer(t["txID"], t["txID"], "TRX", int(v.get("amount") or 0) / 1e6, frm, _ts(t.get("block_timestamp")), to=to))
            elif frm == addr and to != addr:
                out.append(Transfer(f"{t['txID']}:out", t["txID"], "TRX", int(v.get("amount") or 0) / 1e6, frm, _ts(t.get("block_timestamp")),
                                    dir="out", to=to))
        return out

    # --- TON ---
    def ton(self, addr, w=None):
        raw = (ton_raw(addr) or "").upper()

        def fr(x):
            return ton_friendly(x) if x and ":" in x else x
        out = []
        for t in self.get("https://toncenter.com/api/v3/transactions", account=addr, limit=30, sort="desc").get("transactions") or []:
            if (t.get("description") or {}).get("aborted"):
                continue
            m = t.get("in_msg") or {}
            v = int(m.get("value") or 0)
            h = t.get("hash")
            if m.get("source") and v > 0:
                out.append(Transfer(h, h, "TON", v / 1e9, fr(m["source"]), _ts(t.get("now")), to=addr))
            for k, om in enumerate(t.get("out_msgs") or []):
                ov = int(om.get("value") or 0)
                if om.get("destination") and ov > 0 and not (om.get("message_content") or {}).get("decoded", {}).get("type") == "jetton_transfer":
                    out.append(Transfer(f"{h}:out{k}", h, "TON", ov / 1e9, addr, _ts(t.get("now")), dir="out", to=fr(om["destination"])))
        for t in self.get("https://toncenter.com/api/v3/jetton/transfers", owner_address=addr, limit=30, sort="desc").get("jetton_transfers") or []:
            if t.get("transaction_aborted"):
                continue
            dst, src = (t.get("destination") or "").upper(), (t.get("source") or "").upper()
            if dst != raw and src != raw:
                continue
            real = TOKENS["TON"].get((t.get("jetton_master") or "").upper())
            amt = int(t.get("amount") or 0) / 10 ** (real[1] if real else 9)
            o = src == raw and dst != raw
            out.append(Transfer(f"{t.get('transaction_hash')}:j" + ("o" if o else ""), t.get("transaction_hash"), real[0] if real else "JETTON", amt,
                                fr(t.get("source")), _ts(t.get("transaction_now")), None if real else "spam", "out" if o else "in",
                                fr(t.get("destination"))))
        return out

    # --- ETH (Blockscout) ---
    def eth(self, addr, w=None):
        b = f"https://eth.blockscout.com/api/v2/addresses/{addr}"
        me = addr.lower()
        out = []
        for t in self.get(f"{b}/token-transfers", type="ERC-20").get("items") or []:
            to_ = ((t.get("to") or {}).get("hash") or "").lower()
            fr = t.get("from") or {}
            o = (fr.get("hash") or "").lower() == me and to_ != me
            if to_ != me and not o:
                continue
            tk = t.get("token") or {}
            real = TOKENS["ETH"].get((tk.get("address_hash") or tk.get("address") or "").lower())
            tot = t.get("total") or {}
            dec = int(tot.get("decimals") or tk.get("decimals") or 18)
            amt = int(tot.get("value") or 0) / 10 ** dec
            sym = real[0] if real else str(tk.get("symbol") or "?")[:16]
            flag = None if real else ("fake" if sym.upper() in STABLE else "spam")
            if not real and flag == "spam" and tk.get("exchange_rate") and tk.get("reputation") in (None, "ok") and tk.get("holders_count", "0") not in ("0", None):
                flag = None
                self.px[sym] = float(tk["exchange_rate"])
            if fr.get("is_scam") and not o:
                flag = "spam"
            out.append(Transfer(f"{t.get('transaction_hash')}:{t.get('log_index')}" + (":out" if o else ""), t.get("transaction_hash"), sym, amt,
                                fr.get("hash"), _ts(_iso(t.get("timestamp"))), flag, "out" if o else "in", (t.get("to") or {}).get("hash")))
        for t in self.get(f"{b}/transactions").get("items") or []:
            v = int(t.get("value") or 0)
            if v <= 0 or t.get("result") != "success":
                continue
            to_ = ((t.get("to") or {}).get("hash") or "").lower()
            fr_ = ((t.get("from") or {}).get("hash") or "").lower()
            if to_ == me and fr_ != me:
                out.append(Transfer(t["hash"], t["hash"], "ETH", v / 1e18, (t.get("from") or {}).get("hash"), _ts(_iso(t.get("timestamp"))), to=addr))
            elif fr_ == me and to_ != me:
                out.append(Transfer(f"{t['hash']}:out", t["hash"], "ETH", v / 1e18, addr, _ts(_iso(t.get("timestamp"))), dir="out",
                                    to=(t.get("to") or {}).get("hash")))
        return out

    # --- BSC (публичный RPC: события Transfer стейблов + разница баланса BNB) ---
    BSC_RPC = "https://bsc-rpc.publicnode.com"
    TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    def bsc(self, addr, w=None):
        head = int(self.rpc(self.BSC_RPC, "eth_blockNumber", []), 16)
        cur = int(w.cursor) if w is not None and w.cursor and w.cursor.isdigit() else head - 2000
        start = max(cur + 1, head - 4000)
        topic_to = "0x" + "0" * 24 + addr.lower()[2:]
        out = []
        frm = start
        while frm <= head:
            to = min(frm + 4999, head)
            for side in ("in", "out"):
                tp = [self.TRANSFER, None, topic_to] if side == "in" else [self.TRANSFER, topic_to, None]
                logs = self.rpc(self.BSC_RPC, "eth_getLogs", [{"fromBlock": hex(frm), "toBlock": hex(to), "address": list(TOKENS["BSC"]),
                                                               "topics": tp}]) or []
                for lg in logs:
                    sym, dec = TOKENS["BSC"][lg["address"].lower()]
                    amt = int(lg.get("data") or "0x0", 16) / 10 ** dec
                    src, dst = "0x" + lg["topics"][1][-40:], "0x" + lg["topics"][2][-40:]
                    if src == dst:
                        continue
                    out.append(Transfer(f"{lg['transactionHash']}:{int(lg['logIndex'], 16)}" + (":out" if side == "out" else ""), lg["transactionHash"],
                                        sym, amt, src,
                                        _ts(int(lg["blockTimestamp"], 16)) if lg.get("blockTimestamp") else datetime.now(timezone.utc), dir=side, to=dst))
            frm = to + 1
        bal = int(self.rpc(self.BSC_RPC, "eth_getBalance", [addr, "latest"]), 16) / 1e18
        if w is not None:
            prev = (w.balance or {}).get("BNB")
            w.cursor = str(head)
            w.balance = {**(w.balance or {}), "BNB": bal}
            if prev is not None and bal - prev > 1e-9:
                out.append(Transfer(f"bnb:{head}", f"bnb:{head}", "BNB", bal - prev, None, datetime.now(timezone.utc), to=addr))
            elif prev is not None and prev - bal > 1e-9:
                out.append(Transfer(f"bnb:{head}:out", f"bnb:{head}", "BNB", prev - bal, addr, datetime.now(timezone.utc), dir="out"))
        return out

    # --- SOL ---
    SOL_RPC = "https://api.mainnet-beta.solana.com"

    def sol(self, addr, w=None):
        out = []
        opt = {"limit": 10}
        if w is not None and w.cursor:
            opt["until"] = w.cursor
        sigs = self.rpc(self.SOL_RPC, "getSignaturesForAddress", [addr, opt]) or []
        if w is not None and sigs:
            w.cursor = sigs[0]["signature"]
        for s in sigs:
            if s.get("err"):
                continue
            time.sleep(1.0)
            tx = self.rpc(self.SOL_RPC, "getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            meta = tx.get("meta") or {}
            keys = [k.get("pubkey") if isinstance(k, dict) else k for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []]
            at = _ts(tx.get("blockTime") or s.get("blockTime"))
            payer = keys[0] if keys else None
            if addr in keys:
                i = keys.index(addr)
                d = ((meta.get("postBalances") or [0] * (i + 1))[i] - (meta.get("preBalances") or [0] * (i + 1))[i]) / 1e9
                if d > 0 and payer != addr:
                    out.append(Transfer(s["signature"], s["signature"], "SOL", d, payer, at, to=addr))
                elif d < 0 and payer == addr:
                    dst = next((k for j, k in enumerate(keys) if k != addr and j < len(meta.get("postBalances") or [])
                                and (meta["postBalances"][j] - (meta.get("preBalances") or [0] * len(keys))[j]) > 0), None)
                    out.append(Transfer(f"{s['signature']}:out", s["signature"], "SOL", -d, addr, at, dir="out", to=dst))
            pre = {(b.get("mint"), b.get("owner")): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in meta.get("preTokenBalances") or []}
            for b in meta.get("postTokenBalances") or []:
                if b.get("owner") != addr:
                    continue
                mint = b.get("mint")
                d = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) - pre.get((mint, addr), 0.0)
                if not d:
                    continue
                real = TOKENS["SOL"].get(mint)
                if d > 0:
                    out.append(Transfer(f"{s['signature']}:{mint}", s["signature"], real[0] if real else "SPL", d, payer, at, None if real else "spam", to=addr))
                elif real:
                    dst = next((x.get("owner") for x in meta.get("postTokenBalances") or [] if x.get("mint") == mint and x.get("owner") != addr), None)
                    out.append(Transfer(f"{s['signature']}:{mint}:out", s["signature"], real[0], -d, addr, at, dir="out", to=dst))
        return out

    def incoming(self, chain: str, addr: str, w=None) -> list[Transfer]:
        """Переводы адреса. Один и тот же адрес у разных людей читается за проход один раз."""
        if chain in ("BSC", "SOL"):  # курсор хранится у наблюдения — читаем каждое отдельно
            return getattr(self, chain.lower())(addr, w)
        key = (chain, addr)
        if key not in self._seen:
            self._seen[key] = getattr(self, chain.lower())(addr, w)
        return list(self._seen[key])

    # --- открытая проверка отправителя: чёрные списки Tether / Circle ---
    def blacklisted(self, chain: str, addr: str) -> list[str]:
        hits = []
        if chain == "ETH":
            for c, name, sel in (("0xdac17f958d2ee523a2206206994597c13d831ec7", "Tether", "0xe47d6060"),
                                 ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "Circle", "0xfe575a87")):
                r = self.rpc("https://ethereum-rpc.publicnode.com", "eth_call", [{"to": c, "data": sel + "0" * 24 + addr.lower()[2:]}, "latest"])
                if r and int(r, 16):
                    hits.append(name)
        elif chain == "TRON":
            b = b58check(addr)
            if b:
                j = self._j("POST", "https://api.trongrid.io/wallet/triggerconstantcontract",
                            json={"owner_address": addr, "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                                  "function_selector": "isBlackListed(address)", "parameter": "0" * 24 + b[1:].hex(), "visible": True})
                r = (j.get("constant_result") or ["0"])[0]
                if r and int(r, 16):
                    hits.append("Tether")
        return hits


def _iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
