from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token purposes embedded as the "type" claim.
ACCESS = "access"
REFRESH = "refresh"
EMAIL_VERIFY = "email_verify"
PASSWORD_RESET = "password_reset"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def _create_token(subject: dict[str, Any], token_type: str, expires_minutes: int) -> str:
    to_encode = subject.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire, "type": token_type})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(subject: dict[str, Any]) -> str:
    return _create_token(subject, ACCESS, settings.access_token_expire_minutes)


def create_refresh_token(subject: dict[str, Any]) -> str:
    return _create_token(subject, REFRESH, settings.refresh_token_expire_minutes)


def create_email_token(subject: dict[str, Any]) -> str:
    return _create_token(subject, EMAIL_VERIFY, settings.email_token_expire_minutes)


def create_reset_token(subject: dict[str, Any]) -> str:
    return _create_token(subject, PASSWORD_RESET, settings.reset_token_expire_minutes)


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    if expected_type is not None and payload.get("type") != expected_type:
        return None
    return payload


def decode_access_token(token: str) -> dict[str, Any] | None:
    return decode_token(token, expected_type=ACCESS)
