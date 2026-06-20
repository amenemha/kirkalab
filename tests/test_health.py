"""Tests for the liveness/readiness probes (Queue 2.4 monitoring).

Redis is never required to be live in CI: ``app.services.health.check_redis`` is
monkeypatched so we exercise both the healthy and the degraded paths without a
running Redis instance.
"""
from __future__ import annotations

from pathlib import Path

import app.services.health as health_mod

CADDYFILE = Path(__file__).resolve().parents[1] / "deploy" / "Caddyfile"


def test_liveness_is_lightweight_and_ok(client):
    """`/health` must stay a dependency-free 200 with {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


def test_readiness_ok_when_all_dependencies_healthy(client, monkeypatch):
    monkeypatch.setattr(health_mod, "check_redis", lambda url: health_mod.OK)

    response = client.get("/health/ready")

    assert response.status_code == 200
    # Регресс из PR #53: на проде /health/ready отдавал SPA (text/html, 200).
    # Жёстко проверяем JSON content-type, чтобы поймать «уход в SPA-fallback».
    assert response.headers["content-type"].startswith("application/json")
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


def test_unknown_api_path_is_json_404_not_spa(client):
    """Несуществующий /api/* должен отдавать JSON 404, а не SPA index.html.

    На проде /api/* проксируется в бэкенд (см. матчер @backend в Caddyfile),
    поэтому неизвестный путь обязан доходить до FastAPI и возвращать 404 JSON,
    а не перехватываться SPA-fallback с кодом 200/text-html.
    """
    response = client.get("/api/v1/this-route-does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


def test_caddy_backend_matcher_covers_health_subpaths():
    """Регресс PR #53: матчер @backend должен ловить /health/* (readiness).

    Прод-баг: матчер был ``path /api/* /health`` — точный /health, поэтому
    /health/ready уходил в SPA-fallback (index.html, 200). Матчер обязан
    включать health-подпути, иначе readiness снова сломается на проде.
    """
    text = CADDYFILE.read_text(encoding="utf-8")
    matcher_lines = [
        ln.strip() for ln in text.splitlines() if ln.strip().startswith("@backend")
    ]
    assert matcher_lines, "матчер @backend не найден в Caddyfile"
    matcher = matcher_lines[0]
    assert "/api/*" in matcher
    # /health* или явный /health/* — главное, чтобы подпути доходили до бэкенда.
    assert "/health/*" in matcher or "/health*" in matcher
