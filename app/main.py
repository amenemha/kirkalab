from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db import models  # noqa: F401 (ensure models are imported)
from app.db.init_db import ensure_first_admin
from app.db.session import Base, SessionLocal, engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (for dev / simple deployments).
    Base.metadata.create_all(bind=engine)
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

app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
