import secrets

from sqlalchemy import select
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
    for field, value in data.items():
        setattr(user, field, value)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
