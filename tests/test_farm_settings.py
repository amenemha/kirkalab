from app.services.farm.settings import low_hash_alarm
from tests.test_rbac_users import _make_admin


def test_low_hash_on_off_with_hysteresis():
    now = "2026-10-26T10:00:00+00:00"
    assert low_hash_alarm(None, 95.0, 100.0, 90, now) is None
    a = low_hash_alarm(None, 85.0, 100.0, 90, now)
    assert a and a["code"] == "LOWHASH" and a["cls"] == "hashrate" and "85%" in a["msg"]
    # уже горит: при 91% не гаснет (порог +2%), при 93% гаснет; время начала сохраняется
    still = low_hash_alarm(a, 91.0, 100.0, 90, "2026-10-26T11:00:00+00:00")
    assert still and still["since"] == now
    assert low_hash_alarm(a, 93.0, 100.0, 90, now) is None
    assert low_hash_alarm(None, 10.0, None, 90, now) is None


def test_settings_api(client, db):
    token, _ = _make_admin(client, db)
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/farm/settings", headers=h).json()["lowhash_pct"] == 90
    r = client.put("/api/v1/farm/settings", json={"lowhash_pct": 75}, headers=h)
    assert r.status_code == 200 and r.json()["lowhash_pct"] == 75
    assert client.get("/api/v1/farm/settings", headers=h).json()["lowhash_pct"] == 75
    assert client.put("/api/v1/farm/settings", json={"lowhash_pct": 5}, headers=h).status_code == 422
    assert client.put("/api/v1/farm/settings", json={"nope": 1}, headers=h).status_code == 422
    assert client.get("/api/v1/farm/settings").status_code in (401, 403)


def test_history_by_days_and_multisite(client, db):
    token, _ = _make_admin(client, db)
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/v1/farm/history?start=2026-10-01&end=2026-10-07&site=1,2", headers=h)
    assert r.status_code == 200 and r.json() == []
    assert client.get("/api/v1/farm/history?start=2026-10-07&end=2026-10-01", headers=h).status_code == 422
    assert client.get("/api/v1/farm/history?hours=720", headers=h).status_code == 200


def test_retention_noop_on_sqlite(db):
    from app.services.farm.retention import run_retention
    assert run_retention(db, share=0.99)["disk"] == 0.99
