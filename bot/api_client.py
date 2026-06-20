"""Thin async client around the Kirkalab HTTP API.

Wraps the endpoints the bot needs: health check, user registration and
login (JWT). Network/HTTP errors are surfaced as ApiError so handlers can
show a friendly message to the user.
"""
from __future__ import annotations

import httpx


class ApiError(Exception):
  """Raised when the API returns an error or is unreachable."""

  def __init__(self, message: str, status_code: int | None = None) -> None:
    super().__init__(message)
    self.message = message
    self.status_code = status_code


class KirkalabApiClient:
  def __init__(
    self, base_url: str, timeout: float = 10.0, notifier=None
  ) -> None:
    self._base_url = base_url.rstrip("/")
    self._timeout = timeout
    # Optional AdminNotifier; when set, backend 5xx/timeout failures raise an
    # alert (throttled per error type). Kept duck-typed so this module stays
    # free of an aiogram import.
    self._notifier = notifier

  async def _alert_backend(self, error_type: str, detail: str) -> None:
    if self._notifier is None:
      return
    try:
      await self._notifier.alert_backend_error(error_type, detail)
    except Exception:  # noqa: BLE001 — alerting must never break a request
      pass

  async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{self._base_url}{path}"
    try:
      async with httpx.AsyncClient(timeout=self._timeout) as client:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
      await self._alert_backend("timeout", f"{method} {path}")
      raise ApiError(f"API is unreachable: {exc}") from exc
    except httpx.RequestError as exc:
      await self._alert_backend("unreachable", f"{method} {path}")
      raise ApiError(f"API is unreachable: {exc}") from exc
    if response.status_code >= 500:
      await self._alert_backend("5xx", f"{method} {path} -> {response.status_code}")
    return response

  @staticmethod
  def _detail(response: httpx.Response, fallback: str) -> str:
    try:
      data = response.json()
    except ValueError:
      return fallback
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list) and detail:
      first = detail[0]
      if isinstance(first, dict) and "msg" in first:
        return str(first["msg"])
    return str(detail) if detail else fallback

  async def health(self) -> dict:
    response = await self._request("GET", "/health")
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Health check failed"), response.status_code)
    return response.json()

  async def register(self, email: str, handle: str, password: str) -> dict:
    payload = {"email": email, "handle": handle, "password": password}
    response = await self._request("POST", "/api/v1/users/", json=payload)
    if response.status_code != 201:
      raise ApiError(self._detail(response, "Registration failed"), response.status_code)
    return response.json()

  async def login(self, email: str, password: str) -> str:
    payload = {"email": email, "password": password}
    response = await self._request("POST", "/api/v1/auth/login", json=payload)
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Login failed"), response.status_code)
    return response.json()["access_token"]

  async def me(self, access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await self._request("GET", "/api/v1/auth/me", headers=headers)
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not fetch profile"), response.status_code)
    return response.json()

  async def list_brands(self) -> list[dict]:
    """Catalog brands with model counts (public read endpoint)."""
    response = await self._request("GET", "/api/v1/devices/brands")
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load brands"), response.status_code)
    return response.json()

  async def list_models_by_brand(
    self, brand: str, skip: int = 0, limit: int = 8
  ) -> dict:
    """One page of models for a brand. Returns the DeviceModelPage payload
    ({brand, total, skip, limit, items})."""
    params = {"skip": skip, "limit": limit}
    response = await self._request(
      "GET", f"/api/v1/devices/brands/{brand}/models", params=params
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load models"), response.status_code)
    return response.json()

  async def get_model(self, model_id: int) -> dict:
    """Full passport card for a single device model."""
    response = await self._request(
      "GET", f"/api/v1/devices/models/{model_id}"
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load device"), response.status_code)
    return response.json()

  async def list_firmware_presets(self, model_id: int) -> list[dict]:
    """Firmware tuning presets for a device model (may be empty)."""
    params = {"device_model_id": model_id, "limit": 100}
    response = await self._request(
      "GET", "/api/v1/firmware/presets", params=params
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load firmware"), response.status_code)
    return response.json()

  async def internal_profile(
    self, telegram_user_id: int, bot_secret: str
  ) -> dict:
    """Base cabinet for a Telegram user (auto-created on first access).

    Returns the InternalProfile payload ({id, handle, is_pro, is_linked,
    created_at}). FREE users are authenticated automatically by telegram id."""
    headers = {"X-Bot-Secret": bot_secret}
    params = {"telegram_user_id": telegram_user_id}
    response = await self._request(
      "GET", "/api/v1/internal/profile", params=params, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load profile"), response.status_code)
    return response.json()

  async def calc_status(
    self, telegram_user_id: int, bot_secret: str
  ) -> dict:
    """Funnel snapshot for the user's next calc + their saved power price.

    Performs no calculation server-side. Returns the InternalCalcStatus payload
    ({funnel, default_power_price, currency})."""
    headers = {"X-Bot-Secret": bot_secret}
    params = {"telegram_user_id": telegram_user_id}
    response = await self._request(
      "GET", "/api/v1/internal/calc/status", params=params, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load calc status"), response.status_code)
    return response.json()

  async def internal_calc(self, payload: dict, bot_secret: str) -> dict:
    """Run a calculation for a Telegram user; returns the InternalCalcResponse
    payload ({allowed, funnel, result, has_firmware, device_model_id}).

    ``payload`` carries telegram_user_id + either device_model_id or manual
    hashrate_ths/power_w, plus quantity, power_price, currency and flags."""
    headers = {"X-Bot-Secret": bot_secret}
    response = await self._request(
      "POST", "/api/v1/internal/calc", json=payload, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Calculation failed"), response.status_code)
    return response.json()

  async def list_history(
    self, telegram_user_id: int, bot_secret: str, page: int = 0
  ) -> dict:
    """A page of the user's saved calculations (history screen).

    Returns the HistoryPage payload ({items, total, page, page_size, is_pro,
    truncated, retention_days}). Retention is applied server-side."""
    headers = {"X-Bot-Secret": bot_secret}
    params = {"telegram_user_id": telegram_user_id, "page": page}
    response = await self._request(
      "GET", "/api/v1/internal/history", params=params, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load history"), response.status_code)
    return response.json()

  async def get_history_run(
    self, telegram_user_id: int, run_id: int, bot_secret: str
  ) -> dict:
    """One saved calculation for the detail screen (HistoryRunOut payload).

    Raises ApiError(404) when the run is missing or has expired out of the
    retention window."""
    headers = {"X-Bot-Secret": bot_secret}
    params = {"telegram_user_id": telegram_user_id}
    response = await self._request(
      "GET", f"/api/v1/internal/history/{run_id}", params=params, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load calculation"), response.status_code)
    return response.json()

  async def export_calc_xlsx(
    self, telegram_user_id: int, run_id: int, bot_secret: str
  ) -> tuple[bytes, str]:
    """Download a saved calculation as an .xlsx document (PRO-only, Queue 2.2).

    Returns ``(content, filename)`` on success. Raises ApiError with
    ``status_code`` set so the caller can branch:
      * 403 — FREE user, show the soft PRO upsell;
      * 404 — run is missing/expired or not owned by this user.
    The filename is taken from the response's Content-Disposition, falling back
    to a stable ``kirkalab_calc_<id>.xlsx`` if the header is absent."""
    headers = {"X-Bot-Secret": bot_secret}
    params = {"telegram_user_id": telegram_user_id}
    response = await self._request(
      "GET",
      f"/api/v1/internal/calc/{run_id}/export.xlsx",
      params=params,
      headers=headers,
    )
    if response.status_code != 200:
      raise ApiError(
        self._detail(response, "Could not export calculation"),
        response.status_code,
      )
    return response.content, self._filename(response, run_id)

  @staticmethod
  def _filename(response: httpx.Response, run_id: int) -> str:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
      part = part.strip()
      if part.startswith("filename=") and "filename*=" not in part:
        name = part.split("=", 1)[1].strip().strip('"')
        if name:
          return name
    return f"kirkalab_calc_{run_id}.xlsx"

  async def save_power_price(
    self, telegram_user_id: int, power_price: str, bot_secret: str, currency: str = "USDT"
  ) -> dict:
    """Persist the user's default price per kWh (FREE and PRO)."""
    headers = {"X-Bot-Secret": bot_secret}
    payload = {
      "telegram_user_id": telegram_user_id,
      "power_price": power_price,
      "currency": currency,
    }
    response = await self._request(
      "POST", "/api/v1/internal/settings/power-price", json=payload, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not save price"), response.status_code)
    return response.json()

  async def list_plans(self, bot_secret: str) -> list[dict]:
    """Active billing plans for the PRO screen (prices come from the API).

    Returns the list under the PlansResponse ``plans`` key."""
    headers = {"X-Bot-Secret": bot_secret}
    response = await self._request(
      "GET", "/api/v1/internal/plans", headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not load plans"), response.status_code)
    return response.json().get("plans", [])

  async def billing_activate(
    self,
    telegram_id: int,
    plan_code: str,
    telegram_payment_charge_id: str,
    total_amount: int,
    bot_secret: str,
  ) -> dict:
    """Apply a completed Telegram Stars payment; idempotent on the charge id.

    Returns the SubscriptionState payload ({is_pro, plan_code, status,
    started_at, expires_at, premium_until, already_applied})."""
    headers = {"X-Bot-Secret": bot_secret}
    payload = {
      "telegram_id": telegram_id,
      "plan_code": plan_code,
      "telegram_payment_charge_id": telegram_payment_charge_id,
      "total_amount": total_amount,
    }
    response = await self._request(
      "POST", "/api/v1/internal/billing/activate", json=payload, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "Could not activate PRO"), response.status_code)
    return response.json()

  async def approve_qr(
    self, session_id: str, telegram_user_id: int, bot_secret: str
  ) -> dict:
    """Approve a QR-login session on behalf of the Telegram user.

    Sends the X-Bot-Secret header and the exact QrApproveRequest body the
    API expects: {"session_id": ..., "telegram_user_id": ...}.
    """
    headers = {"X-Bot-Secret": bot_secret}
    payload = {"session_id": session_id, "telegram_user_id": telegram_user_id}
    response = await self._request(
      "POST", "/api/v1/auth/qr/approve", json=payload, headers=headers
    )
    if response.status_code != 200:
      raise ApiError(self._detail(response, "QR approval failed"), response.status_code)
    return response.json()
