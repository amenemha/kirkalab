import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db import models
from app.schemas.users import UserCreate, UserUpdate


def get_user(db: Session, user_id: int) -> models.User | None:
    return db.get(models.User, user_id)


def get_user_by_email(db: Session, email: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.email == email))


def get_user_by_telegram_id(db: Session, telegram_user_id: int) -> models.User | None:
    return db.scalar(
        select(models.User).where(models.User.telegram_user_id == telegram_user_id)
    )


def get_or_create_telegram_user(db: Session, telegram_user_id: int) -> models.User:
    user = get_user_by_telegram_id(db, telegram_user_id=telegram_user_id)
    if user is not None:
        return user
    user = models.User(
        # Placeholder address: telegram users have no real email. The domain
        # must still pass EmailStr validation used by the user schemas, so a
        # reserved TLD like ".local" cannot be used here.
        email=f"tg_{telegram_user_id}@telegram.bot",
        handle=f"tg_{telegram_user_id}",
        # No usable password: store a random hash so the account cannot be
        # logged into via the password flow.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        telegram_user_id=telegram_user_id,
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_handle(db: Session, handle: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.handle == handle))


def get_users(db: Session, skip: int = 0, limit: int = 100) -> list[models.User]:
    stmt = select(models.User).offset(skip).limit(limit)
    return list(db.scalars(stmt).all())


def create_user(db: Session, user_in: UserCreate) -> models.User:
    user = models.User(
        email=user_in.email,
        handle=user_in.handle,
        hashed_password=hash_password(user_in.password),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user



def delete_user(db: Session, user: models.User) -> None:
    db.delete(user)
    db.commit()



def update_user(db: Session, user: models.User, user_in: UserUpdate) -> models.User:
    data = user_in.model_dump(exclude_unset=True)
    if "password" in data:
        user.hashed_password = hash_password(data.pop("password"))
        # Invalidate every outstanding refresh token for this user: bumping the
        # version makes previously issued refresh tokens fail the version check.
        user.token_version = (user.token_version or 0) + 1
    for field, value in data.items():
        setattr(user, field, value)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_or_create_settings(db: Session, user_id: int) -> models.UserSettings:
    settings = db.scalar(
        select(models.UserSettings).where(models.UserSettings.user_id == user_id)
    )
    if settings is not None:
        return settings
    settings = models.UserSettings(user_id=user_id)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def set_default_power_price(
    db: Session, user_id: int, power_price, currency: str = "USDT"
) -> models.UserSettings:
    settings = get_or_create_settings(db, user_id=user_id)
    settings.default_power_price = power_price
    settings.currency = currency
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def is_token_revoked(db: Session, jti: str) -> bool:
    return (
        db.scalar(
            select(models.RevokedToken.id).where(models.RevokedToken.jti == jti)
        )
        is not None
    )


def revoke_token(db: Session, jti: str, expires_at: datetime) -> None:
    """Blacklist a refresh-token ``jti`` until it would expire anyway.

    Idempotent: a ``jti`` that is already revoked is left untouched so a
    double-spend of the same token does not raise.
    """
    if is_token_revoked(db, jti):
        return
    db.add(models.RevokedToken(jti=jti, expires_at=expires_at))
    db.commit()


def prune_revoked_tokens(db: Session) -> int:
    """Delete blacklist rows whose tokens have already expired.

    An expired JWT is rejected by signature/expiry validation regardless, so
    keeping it on the blacklist serves no purpose. Returns the number removed.
    """
    result = db.execute(
        delete(models.RevokedToken).where(
            models.RevokedToken.expires_at < datetime.now(timezone.utc)
        )
    )
    db.commit()
    return result.rowcount or 0
