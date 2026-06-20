"""Integration tests for the internal history endpoints (Queue 2.3).

Covers: retention filter (FREE 3-day window vs PRO unbounded), pagination,
empty state, detail fetch, and that an expired run can't be opened directly."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.crud import calc as crud_calc
from app.crud import users as crud_users
from app.db import models

BOT_SECRET = "test-bot-secret"


def _headers():
    return {"X-Bot-Secret": BOT_SECRET}


def _make_user(db, tg_id: int, *, is_pro: bool = False) -> models.User:
    user = crud_users.get_or_create_telegram_user(db, telegram_user_id=tg_id)
    if is_pro:
        user.is_pro = True
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _add_run(db, user_id: int, *, age_days: float, name: str = "ASIC") -> models.CalculationRun:
    run = crud_calc.record_run(
        db,
        user_id=user_id,
        device_model_id=None,
        device_name=name,
        hashrate_ths=Decimal("100.00"),
        power_w=3250,
        quantity=1,
        power_price=Decimal("0.0500"),
        currency="USDT",
        net_profit_day_usdt=Decimal("1.23"),
        net_profit_month_usdt=Decimal("37.00"),
    )
    # Backdate created_at to simulate age.
    run.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# --------------------------------------------------------------------------- #
# Auth.
# --------------------------------------------------------------------------- #
def test_history_requires_bot_secret(client):
    resp = client.get(
        "/api/v1/internal/history", params={"telegram_user_id": 1}
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Empty state.
# --------------------------------------------------------------------------- #
def test_history_empty_for_new_user(client):
    resp = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800001},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["truncated"] is False


# --------------------------------------------------------------------------- #
# Retention: FREE shows only the last N days; PRO shows everything.
# --------------------------------------------------------------------------- #
def test_free_retention_hides_old_runs(client, db):
    user = _make_user(db, 800010, is_pro=False)
    _add_run(db, user.id, age_days=0.1, name="Fresh")     # within window
    _add_run(db, user.id, age_days=1.0, name="Yesterday")  # within window
    _add_run(db, user.id, age_days=5.0, name="Old")        # outside 3-day window

    resp = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800010},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    names = [it["device_name"] for it in body["items"]]
    assert "Old" not in names
    assert body["truncated"] is True
    assert body["retention_days"] == 3
    assert body["is_pro"] is False


def test_pro_retention_shows_all_runs(client, db):
    user = _make_user(db, 800011, is_pro=True)
    _add_run(db, user.id, age_days=0.1, name="Fresh")
    _add_run(db, user.id, age_days=30.0, name="Month old")
    _add_run(db, user.id, age_days=400.0, name="Year old")

    resp = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800011},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["truncated"] is False
    assert body["retention_days"] == 0
    assert body["is_pro"] is True


# --------------------------------------------------------------------------- #
# Ordering + pagination.
# --------------------------------------------------------------------------- #
def test_history_newest_first(client, db):
    user = _make_user(db, 800020, is_pro=True)
    _add_run(db, user.id, age_days=2.0, name="Older")
    _add_run(db, user.id, age_days=0.1, name="Newer")

    body = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800020},
        headers=_headers(),
    ).json()
    assert [it["device_name"] for it in body["items"]] == ["Newer", "Older"]


def test_history_pagination(client, db):
    user = _make_user(db, 800021, is_pro=True)
    # 7 runs, ascending age so newest is run #6 ("r6").
    for i in range(7):
        _add_run(db, user.id, age_days=float(i) * 0.01, name=f"r{i}")

    page0 = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800021, "page": 0},
        headers=_headers(),
    ).json()
    assert page0["total"] == 7
    assert page0["page"] == 0
    assert page0["page_size"] == 5
    assert len(page0["items"]) == 5

    page1 = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800021, "page": 1},
        headers=_headers(),
    ).json()
    assert page1["page"] == 1
    assert len(page1["items"]) == 2


def test_history_page_out_of_range_clamped(client, db):
    user = _make_user(db, 800022, is_pro=True)
    _add_run(db, user.id, age_days=0.1)

    body = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800022, "page": 99},
        headers=_headers(),
    ).json()
    # Clamped to the last (only) page.
    assert body["page"] == 0
    assert len(body["items"]) == 1


# --------------------------------------------------------------------------- #
# Detail.
# --------------------------------------------------------------------------- #
def test_history_detail_returns_snapshot(client, db):
    user = _make_user(db, 800030, is_pro=True)
    run = _add_run(db, user.id, age_days=0.1, name="Antminer S21")

    resp = client.get(
        f"/api/v1/internal/history/{run.id}",
        params={"telegram_user_id": 800030},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_name"] == "Antminer S21"
    assert Decimal(body["net_profit_day_usdt"]) == Decimal("1.23")
    assert Decimal(body["net_profit_month_usdt"]) == Decimal("37.00")
    assert body["power_w"] == 3250


def test_history_detail_other_user_404(client, db):
    owner = _make_user(db, 800031, is_pro=True)
    run = _add_run(db, owner.id, age_days=0.1)

    resp = client.get(
        f"/api/v1/internal/history/{run.id}",
        params={"telegram_user_id": 800032},  # different user
        headers=_headers(),
    )
    assert resp.status_code == 404


def test_history_detail_expired_run_404_on_free(client, db):
    user = _make_user(db, 800033, is_pro=False)
    run = _add_run(db, user.id, age_days=10.0)  # outside 3-day window

    resp = client.get(
        f"/api/v1/internal/history/{run.id}",
        params={"telegram_user_id": 800033},
        headers=_headers(),
    )
    assert resp.status_code == 404


def test_history_detail_expired_run_visible_on_pro(client, db):
    user = _make_user(db, 800034, is_pro=True)
    run = _add_run(db, user.id, age_days=10.0)

    resp = client.get(
        f"/api/v1/internal/history/{run.id}",
        params={"telegram_user_id": 800034},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# Snapshot is written by a real calc through /internal/calc.
# --------------------------------------------------------------------------- #
def test_calc_records_device_name_and_month_snapshot(client, db, monkeypatch):
    from app.services.market import service as market_service
    from app.services.market.provider import RawMarketData

    market_service.reset_cache()
    monkeypatch.setattr(
        market_service,
        "fetch_market_data",
        lambda: RawMarketData(
            price_usdt=Decimal("60000"),
            network_difficulty=Decimal("80000000000000"),
            block_reward_btc=Decimal("3.125"),
        ),
    )

    resp = client.post(
        "/api/v1/internal/calc",
        json={
            "telegram_user_id": 800040,
            "hashrate_ths": "100",
            "power_w": 3250,
            "quantity": 1,
            "power_price": "0.05",
            "currency": "USDT",
            "device_name": "Своё оборудование",
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    market_service.reset_cache()

    body = client.get(
        "/api/v1/internal/history",
        params={"telegram_user_id": 800040},
        headers=_headers(),
    ).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["device_name"] == "Своё оборудование"
    assert item["net_profit_month_usdt"] is not None
