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
  def __init__(self, base_url: str, timeout: float = 10.0) -> None:
    self._base_url = base_url.rstrip("/")
    self._timeout = timeout

  async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{self._base_url}{path}"
    try:
      async with httpx.AsyncClient(timeout=self._timeout) as client:
        return await client.request(method, url, **kwargs)
    except httpx.RequestError as exc:
      raise ApiError(f"API is unreachable: {exc}") from exc

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
