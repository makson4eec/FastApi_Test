from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Payment


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def app(database_url):
    return create_app(Settings(database_url=database_url))


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def payment_data(client):
    tariffs = client.get("/tariffs").json()
    standard = next(tariff for tariff in tariffs if tariff["title"] == "standard")
    return {"tariff_id": standard["id"], "email": "student@example.com", "method": "card"}


@pytest.fixture
def payment_count(app, client):
    def count() -> int:
        with app.state.session_factory() as session:
            return session.scalar(select(func.count()).select_from(Payment))

    return count
