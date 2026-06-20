"""Readiness checks for the backend (Queue 2.4 monitoring).

Liveness (`/health`) stays trivial and dependency-free so Docker's healthcheck
never reports the container down just because Postgres or Redis blipped. The
readiness probe (`/health/ready`) actively checks the external dependencies the
app needs to serve traffic: the database (``SELECT 1``) and Redis (``PING``).

Both checks are best-effort and never raise: a failure is reported as ``"fail"``
in the payload, and the caller decides the HTTP status. Redis is pinged with a
lazily-imported client so the backend (and its SQLite/no-redis CI) does not
require the ``redis`` package to import this module.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("kirkalab.health")

OK = "ok"
FAIL = "fail"


def check_database(db: Session) -> str:
    """Return ``"ok"`` if a trivial query succeeds, ``"fail"`` otherwise."""
    try:
        db.execute(text("SELECT 1"))
        return OK
    except Exception:  # noqa: BLE001 — readiness must never raise
        logger.warning("Readiness DB check failed", exc_info=True)
        return FAIL


def check_redis(redis_url: str) -> str:
    """Ping Redis at ``redis_url``; ``"ok"`` on PONG, ``"fail"`` otherwise.

    The ``redis`` client is imported lazily so environments without it (and the
    CI that mocks it) can still import this module.
    """
    try:
        import redis  # noqa: PLC0415 — lazy so redis stays an optional backend dep
    except ImportError:
        logger.warning("Readiness Redis check skipped: redis package not installed")
        return FAIL
    client = None
    try:
        client = redis.Redis.from_url(
            redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        return OK if client.ping() else FAIL
    except Exception:  # noqa: BLE001 — readiness must never raise
        logger.warning("Readiness Redis check failed", exc_info=True)
        return FAIL
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def readiness_report(db: Session, redis_url: str, version: str) -> dict:
    """Aggregate dependency checks into the readiness payload.

    ``status`` is ``"ok"`` only when every dependency is healthy, otherwise
    ``"degraded"``. The caller maps that to a 200/503 HTTP status.
    """
    from datetime import datetime, timezone

    db_status = check_database(db)
    redis_status = check_redis(redis_url)
    overall = OK if db_status == OK and redis_status == OK else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "redis": redis_status,
        "version": version,
        "time": datetime.now(timezone.utc).isoformat(),
    }
