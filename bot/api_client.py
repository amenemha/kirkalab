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
