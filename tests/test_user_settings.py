from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models


def _make_user(db, handle="settings_user"):
    user = models.User(
        email=f"{handle}@example.com",
        handle=handle,
        hashed_password="x",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_create_and_read_settings(db):
    user = _make_user(db)
    settings = models.UserSettings(
        user_id=user.id,
        default_power_price=Decimal("0.0750"),
    )
    db.add(settings)
    db.commit()

    loaded = (
        db.query(models.UserSettings).filter_by(user_id=user.id).one()
    )
    assert loaded.default_power_price == Decimal("0.0750")
    # currency defaults to USDT
    assert loaded.currency == "USDT"
    assert loaded.language is None
    assert loaded.hide_small_assets is False


def test_user_id_is_unique(db):
    user = _make_user(db, handle="unique_user")
    db.add(models.UserSettings(user_id=user.id))
    db.commit()

    db.add(models.UserSettings(user_id=user.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.query(models.UserSettings).filter_by(user_id=user.id).count() == 1
