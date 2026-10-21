import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.health import get_ollama
from app.core.config import get_settings
from app.db.session import get_engine
from app.llm.ollama_client import OllamaClient
from app.main import app
from tests.conftest import fake_ollama_transport

# Set COB_TEST_DB_URL to run against a real Postgres (e.g. the docker compose one).
DB_URL = os.environ.get("COB_TEST_DB_URL")


def _client(settings, models, engine):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_ollama] = lambda: OllamaClient(
        settings, transport=fake_ollama_transport(models, settings.embed_dim)
    )
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_health_degraded_when_db_down_and_models_missing(settings):
    engine = create_engine("postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    resp = _client(settings, [f"{settings.embed_model}:latest"], engine).get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["database"]["ok"] is False
    assert body["ollama"]["missing"] == [settings.chat_model]


@pytest.mark.skipif(not DB_URL, reason="COB_TEST_DB_URL not set")
def test_health_ok_against_real_postgres(settings):
    engine = create_engine(DB_URL)
    models = [settings.chat_model, settings.sql_model, f"{settings.embed_model}:latest"]
    resp = _client(settings, models, engine).get("/health")
    body = resp.json()
    assert resp.status_code == 200, body
    assert set(body["database"]["extensions"]) == {"vector", "pg_trgm"}
