"""Tests for the liveness/readiness probes (Queue 2.4 monitoring).

Redis is never required to be live in CI: ``app.services.health.check_redis`` is
monkeypatched so we exercise both the healthy and the degraded paths without a
running Redis instance.
"""
from __future__ import annotations

import app.services.health as health_mod


def test_liveness_is_lightweight_and_ok(client):
    """`/health` must stay a dependency-free 200 with {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok_when_all_dependencies_healthy(client, monkeypatch):
    monkeypatch.setattr(health_mod, "check_redis", lambda url: health_mod.OK)

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"
    assert "version" in body
    assert "time" in body


def test_readiness_degraded_503_when_redis_down(client, monkeypatch):
    monkeypatch.setattr(health_mod, "check_redis", lambda url: health_mod.FAIL)

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] == "ok"
    assert body["redis"] == "fail"


def test_readiness_degraded_503_when_db_down(client, monkeypatch):
    monkeypatch.setattr(health_mod, "check_database", lambda db: health_mod.FAIL)
    monkeypatch.setattr(health_mod, "check_redis", lambda url: health_mod.OK)

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] == "fail"


def test_check_database_reports_fail_on_broken_session():
    class _BrokenSession:
        def execute(self, *_a, **_k):
            raise RuntimeError("connection refused")

    assert health_mod.check_database(_BrokenSession()) == health_mod.FAIL
