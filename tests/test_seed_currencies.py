"""The currency catalog is seeded and idempotent (mirrors seed_plans)."""
from app.crud import fx as crud_fx
from app.db.seed_currencies import CURRENCIES, seed_currencies


def test_currencies_present_after_seed(db):
    # conftest already seeds; assert the full set is there with USDT as anchor.
    codes = {c.code for c in crud_fx.list_currencies(db, active_only=False)}
    assert {"USDT", "USD", "RUB", "KZT", "UAH", "EUR"} <= codes

    usdt = crud_fx.get_currency(db, "USDT")
    assert usdt is not None
    assert usdt.is_fiat is False
    rub = crud_fx.get_currency(db, "RUB")
    assert rub is not None
    assert rub.is_fiat is True
    assert rub.symbol == "₽"


def test_seed_is_idempotent(db):
    before = len(crud_fx.list_currencies(db, active_only=False))
    # Re-seed through the same session's bind so the in-memory SQLite connection
    # (StaticPool) is reused and the freshly-created table is visible.
    touched = seed_currencies(db.get_bind())
    db.expire_all()
    after = len(crud_fx.list_currencies(db, active_only=False))
    assert touched == len(CURRENCIES)
    assert before == after  # no duplicates created on re-run
