"""Minimal read-only CROFT public API client."""
from __future__ import annotations

import time

import httpx


class CroftClient:
    def __init__(self, base_url: str, login: str, password: str, timeout: float = 60.0):
        self.base = base_url.rstrip("/")
        self.login = login
        self.password = password
        self.timeout = timeout
        self._token: str | None = None
        self._token_at = 0.0

    def _auth(self, client: httpx.Client) -> str:
        if self._token and time.time() - self._token_at < 1800:
            return self._token
        r = client.post(f"{self.base}/getToken", json={"userId": self.login, "password": self.password})
        r.raise_for_status()
        self._token = r.json()["token"]
        self._token_at = time.time()
        return self._token

    def _get(self, path: str, params: dict | None = None):
        with httpx.Client(timeout=self.timeout) as c:
            for attempt in (1, 2):
                tok = self._auth(c)
                r = c.get(f"{self.base}/{path}", params=params, headers={"Authorization": f"Bearer {tok}"})
                if r.status_code == 401 and attempt == 1:
                    self._token = None
                    continue
                r.raise_for_status()
                return r.json()

    def view_groups(self) -> list[dict]:
        return self._get("viewGroups")

    def devices(self, customer_id: str, client_id: str | None = None) -> list[dict]:
        p = {"customerUniqId": customer_id}
        if client_id:
            p["hosterClientUniqId"] = client_id
        data = self._get("devices", p)
        return data.get("devices", []) if isinstance(data, dict) else data

    def iter_devices(self, customer_id: str, client_id: str | None = None):
        """Асики по одному, без загрузки всего ответа в память (у крупных ферм он десятки МБ)."""
        import ijson

        p = {"customerUniqId": customer_id}
        if client_id:
            p["hosterClientUniqId"] = client_id
        with httpx.Client(timeout=self.timeout) as c:
            for attempt in (1, 2):
                tok = self._auth(c)
                with c.stream("GET", f"{self.base}/devices", params=p, headers={"Authorization": f"Bearer {tok}"}) as r:
                    if r.status_code == 401 and attempt == 1:
                        self._token = None
                        continue
                    r.raise_for_status()
                    src = _Stream(r.iter_bytes())
                    prefix = "item" if src.first() == b"[" else "devices.item"
                    yield from ijson.items(src, prefix, use_float=True)
                    return


class _Stream:
    """Файлоподобная обёртка над потоком байтов для ijson; first() — первый значимый байт."""

    def __init__(self, it):
        self._it = iter(it)
        self._buf = b""

    def first(self) -> bytes:
        while not self._buf.lstrip():
            chunk = next(self._it, None)
            if chunk is None:
                return b""
            self._buf += chunk
        return self._buf.lstrip()[:1]

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            chunk = next(self._it, None)
            if chunk is None:
                break
            self._buf += chunk
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out
