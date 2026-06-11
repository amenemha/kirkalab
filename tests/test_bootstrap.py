from app.core.config import Settings
from app.db import models
from app.db.init_db import ensure_first_admin


def _admin_settings(**overrides) -> Settings:
    values = {
        "first_admin_email": "admin@example.com",
        "first_admin_handle": "admin",
        "first_admin_password": "sup3r-secret-pw",
    }
    values.update(overrides)
    return Settings(**values)


def test_creates_first_admin_when_configured(db):
    user = ensure_first_admin(db, _admin_settings())

    assert user is not None
    assert user.email == "admin@example.com"
    assert user.handle == "admin"
    assert user.is_admin is True
    assert user.is_active is True
    # Password must be hashed, never stored in plaintext.
    assert user.hashed_password != "sup3r-secret-pw"


def test_bootstrap_is_idempotent(db):
    first = ensure_first_admin(db, _admin_settings())
    assert first is not None

    # A second call with the same email must not create a duplicate.
    second = ensure_first_admin(db, _admin_settings())
    assert second is None

    count = (
        db.query(models.User)
        .filter(models.User.email == "admin@example.com")
        .count()
    )
    assert count == 1


def test_skips_when_settings_incomplete(db):
    user = ensure_first_admin(db, _admin_settings(first_admin_password=None))

    assert user is None
    count = db.query(models.User).count()
    assert count == 0
