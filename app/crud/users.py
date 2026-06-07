from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db import models
from app.schemas.users import UserCreate


def get_user(db: Session, user_id: int) -> models.User | None:
    return db.get(models.User, user_id)


def get_user_by_email(db: Session, email: str) -> models.User | None:
    return db.scalar(select(models.User).where(models.User.email == email))


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
