from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy.orm import Session
from starlette.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.init_db import ensure_first_admin
from app.db.session import SessionLocal, get_db
from app.services.health import readiness_report

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bootstrap the first admin user if FIRST_ADMIN_* settings are provided.
    db = SessionLocal()
    try:
        ensure_first_admin(db, settings)
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness probe — intentionally trivial and dependency-free.

    Docker's healthcheck and external monitoring curl this and expect a 200 with
    ``{"status": "ok"}``. Do not add external dependency checks here; readiness
    lives in ``/health/ready``.
    """
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready(response: Response, db: Session = Depends(get_db)) -> dict:
    """Readiness probe — checks the DB and Redis the app depends on.

    Returns 200 when every dependency is healthy and 503 (Service Unavailable)
    when any is down, so an orchestrator can pull the instance out of rotation
    without the liveness probe (and thus the container) being affected.
    """
    report = readiness_report(db, settings.redis_url, settings.app_version)
    if report["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
