import os

# Configure the application before importing its modules.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DEBUG"] = "false"
os.environ["AUTH_SECURE_COOKIE"] = "false"
os.environ["MONITORING_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import hash_password
from app.db.database import Base, get_db
from app.db.models import User
from main import app


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    yield engine

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture()
def anonymous_client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


TEST_PASSWORD = "test-password-for-auth"


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(TEST_PASSWORD)


@pytest.fixture()
def accounts(session_factory, password_hash):
    with session_factory() as db:
        db.add_all(
            User(username=role, role=role, password_hash=password_hash)
            for role in ("admin", "viewer")
        )
        db.commit()


@pytest.fixture()
def client(anonymous_client, accounts):
    response = anonymous_client.post(
        "/auth/login", json={"username": "admin", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    return anonymous_client
