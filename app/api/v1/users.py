from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_admin
from app.crud import users as crud_users
from app.db import models
from app.db.session import get_db
from app.schemas.users import UserCreate, UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserRead])
def list_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin),
) -> list[models.User]:
    return crud_users.get_users(db, skip=skip, limit=limit)


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(user_in: UserCreate, db: Session = Depends(get_db)) -> models.User:
    if crud_users.get_user_by_email(db, email=user_in.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )
    if crud_users.get_user_by_handle(db, handle=user_in.handle):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this handle already exists",
        )
    return crud_users.create_user(db, user_in=user_in)


@router.get("/{user_id}", response_model=UserRead)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin),
) -> models.User:
    user = crud_users.get_user(db, user_id=user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user
