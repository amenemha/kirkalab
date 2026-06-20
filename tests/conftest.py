import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("BOT_INTERNAL_SECRET", "test-bot-secret")

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite://"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_db() -> Generator:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _setup_database() -> Generator:
    Base.metadata.create_all(bind=engine)
    # Seed billing plans so the migration's seed is mirrored for the in-memory
    # test DB (create_all does not run data migrations).
    from app.db.seed_plans import seed_plans

    seed_plans(engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db() -> Generator:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
