"""First-admin bootstrap utilities.

Creates an initial admin user on application startup when the
FIRST_ADMIN_* settings are provided. This solves the bootstrap problem
where every user is created with is_admin=False and there would otherwise
be no way to grant the very first admin privileges.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db import models

logger = logging.getLogger(__name__)


def ensure_first_admin(db: Session, settings: Settings | None = None) -> models.User | None:
    """Create the first admin user if configured and not already present.

    Returns the created user, or None if no action was taken. The operation
    is idempotent: if a user with FIRST_ADMIN_EMAIL already exists, nothing
    is changed.
    """
    settings = settings or get_settings()

    email = settings.first_admin_email
    handle = settings.first_admin_handle
    password = settings.first_admin_password

    if not (email and handle and password):
        logger.info("First-admin bootstrap skipped: FIRST_ADMIN_* not fully set")
        return None

    existing = db.scalar(select(models.User).where(models.User.email == email))
    if existing is not None:
        logger.info("First-admin bootstrap skipped: user already exists")
        return None

    admin = models.User(
        email=email,
        handle=handle,
        hashed_password=hash_password(password),
        is_active=True,
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info("First-admin bootstrap: created admin user %s", email)
    return admin
