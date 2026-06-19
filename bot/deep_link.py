"""Parsing of Telegram ``/start`` deep-link payloads.

Kept dependency-free (no aiogram import) so it can be unit-tested in
environments where the Telegram libraries are not installed.
"""
from __future__ import annotations

QR_PREFIX = "qr_"


def parse_qr_payload(payload: str | None) -> str | None:
  """Extract the session id from a ``/start`` deep-link payload.

  Returns the session id for a ``qr_<session_id>`` payload, or ``None`` when
  the payload is missing, empty, not a QR payload, or has no id after the
  prefix.
  """
  if not payload:
    return None
  payload = payload.strip()
  if not payload.startswith(QR_PREFIX):
    return None
  session_id = payload[len(QR_PREFIX):]
  return session_id or None
