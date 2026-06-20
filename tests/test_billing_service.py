"""Unit tests for the billing service + entitlement (no aiogram, no HTTP).

Covers activation, idempotency on the charge id, renewal stacking, and lazy
expiration / the expire_subscriptions sweep."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.services.billing import service as billing
from app.services.billing import entitlement


@pytest.fixture
def session(db):
    """Alias for the conftest ``db`` session fixture (shared in-memory DB)."""
    return db


def _aware(value: datetime) -> datetime:
    """SQLite round-trips naive datetimes; treat them as UTC for comparison."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _make_user(session, tg_id=900001) -> models.User:
    user = models.User(
        email=f"tg_{tg_id}@telegram.bot",
        handle=f"tg_{tg_id}",
        hashed_password="x",
        telegram_user_id=tg_id,
        is_pro=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_activate_sets_pro_and_expiry(session):
    user = _make_user(session)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sub = billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-A",
        total_amount=250,
        now=now,
    )
    assert sub.status == "active"
    assert _aware(sub.expires_at) == now + timedelta(days=30)
    session.refresh(user)
    assert user.is_pro is True
    assert entitlement.is_pro(user, now=now) is True


def test_activate_is_idempotent_on_charge_id(session):
    user = _make_user(session, tg_id=900002)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-DUP",
        total_amount=250,
        now=now,
    )
    # Re-deliver the same charge id (Telegram retry): no new row, no extension.
    second = billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-DUP",
        total_amount=250,
        now=now + timedelta(days=5),
    )
    assert first.id == second.id
    assert second.expires_at == first.expires_at
    count = session.query(models.Subscription).filter_by(user_id=user.id).count()
    assert count == 1


def test_renewal_stacks_on_remaining_time(session):
    user = _make_user(session, tg_id=900003)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-1",
        total_amount=250,
        now=now,
    )
    # Buy again 10 days later, while 20 days remain: new expiry stacks on top.
    later = now + timedelta(days=10)
    sub2 = billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-2",
        total_amount=250,
        now=later,
    )
    # Previous expiry was now+30; stacking adds another 30 -> now+60.
    assert _aware(sub2.expires_at) == now + timedelta(days=60)


def test_renewal_after_lapse_starts_from_now(session):
    user = _make_user(session, tg_id=900004)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-1",
        total_amount=250,
        now=now,
    )
    # Buy again well after expiry: the new period starts from the purchase time.
    much_later = now + timedelta(days=100)
    sub2 = billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-late",
        total_amount=250,
        now=much_later,
    )
    assert _aware(sub2.expires_at) == much_later + timedelta(days=30)


def test_unknown_plan_raises(session):
    user = _make_user(session, tg_id=900005)
    with pytest.raises(billing.BillingError):
        billing.activate_subscription(
            session,
            user=user,
            plan_code="nope",
            telegram_payment_charge_id="charge-x",
            total_amount=10,
        )


def test_free_plan_not_purchasable(session):
    user = _make_user(session, tg_id=900006)
    with pytest.raises(billing.BillingError):
        billing.activate_subscription(
            session,
            user=user,
            plan_code="free",
            telegram_payment_charge_id="charge-free",
            total_amount=0,
        )


def test_lazy_expiration_flips_flag_on_read(session):
    user = _make_user(session, tg_id=900007)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-1",
        total_amount=250,
        now=now,
    )
    after = now + timedelta(days=31)
    # is_pro (pure) sees the lapse without writing.
    assert entitlement.is_pro(user, now=after) is False
    # reconcile persists the FREE flag + expires the subscription.
    entitlement.reconcile_user(session, user, now=after)
    session.refresh(user)
    assert user.is_pro is False
    sub = session.query(models.Subscription).filter_by(user_id=user.id).one()
    assert sub.status == "expired"


def test_expire_subscriptions_sweep(session):
    user = _make_user(session, tg_id=900008)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    billing.activate_subscription(
        session,
        user=user,
        plan_code="pro_monthly",
        telegram_payment_charge_id="charge-1",
        total_amount=250,
        now=now,
    )
    expired = entitlement.expire_subscriptions(
        session, now=now + timedelta(days=40)
    )
    assert expired == 1
    session.refresh(user)
    assert user.is_pro is False


def test_manual_pro_without_expiry_stays_pro(session):
    # A user flagged PRO with no premium_until (legacy/manual) is always PRO.
    user = _make_user(session, tg_id=900009)
    user.is_pro = True
    user.premium_until = None
    session.add(user)
    session.commit()
    assert entitlement.is_pro(user) is True
