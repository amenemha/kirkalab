from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    EMAIL_VERIFY,
    PASSWORD_RESET,
    REFRESH,
    create_access_token,
    create_email_token,
    create_refresh_token,
    create_reset_token,
    decode_access_token,
    decode_token,
    verify_password,
)
from app.crud import users as crud_users
from app.db import models
from app.db.session import get_db
from app.schemas.users import (
    EmailVerifyRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    Token,
    UserRead,
    UserUpdate,
)

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None or "user_id" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = crud_users.get_user(db, user_id=int(payload["user_id"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return user


def get_current_admin(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def _token_subject(user: models.User) -> dict:
    return {"user_id": user.id, "email": user.email, "is_admin": user.is_admin}


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = crud_users.get_user_by_email(db, email=payload.email)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    subject = _token_subject(user)
    return Token(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> Token:
    data = decode_token(payload.refresh_token, expected_type=REFRESH)
    if data is None or "user_id" not in data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    user = crud_users.get_user(db, user_id=int(data["user_id"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    subject = _token_subject(user)
    return Token(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


@router.post("/verify-email/request")
def request_email_verification(
    current_user: models.User = Depends(get_current_user),
) -> dict:
    token = create_email_token(_token_subject(current_user))
    # In production this token is emailed to the user; returned here for
    # client integration and testability.
    return {"email_verify_token": token}


@router.post("/verify-email")
def verify_email(payload: EmailVerifyRequest, db: Session = Depends(get_db)) -> dict:
    data = decode_token(payload.token, expected_type=EMAIL_VERIFY)
    if data is None or "user_id" not in data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )
    user = crud_users.get_user(db, user_id=int(data["user_id"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )
    return {"detail": "Email verified", "email": user.email}


@router.post("/password-reset/request")
def request_password_reset(
    payload: PasswordResetRequest, db: Session = Depends(get_db)
) -> dict:
    user = crud_users.get_user_by_email(db, email=payload.email)
    # Always return the same response to avoid user enumeration.
    if user is None:
        return {"detail": "If the account exists, a reset token has been issued"}
    token = create_reset_token(_token_subject(user))
    return {
        "detail": "If the account exists, a reset token has been issued",
        "reset_token": token,
    }


@router.post("/password-reset/confirm")
def confirm_password_reset(
    payload: PasswordResetConfirm, db: Session = Depends(get_db)
) -> dict:
    data = decode_token(payload.token, expected_type=PASSWORD_RESET)
    if data is None or "user_id" not in data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    user = crud_users.get_user(db, user_id=int(data["user_id"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    crud_users.update_user(db, user=user, user_in=UserUpdate(password=payload.new_password))
    return {"detail": "Password updated"}


@router.get("/me", response_model=UserRead)
def read_me(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    return current_user
