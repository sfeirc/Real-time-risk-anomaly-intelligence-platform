import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import state
from app.config import settings
from app.routes import alerts, auth, models, system


class FakeClickHouse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_rows: list[dict] = []

    async def query_rows(self, query: str, params: dict | None = None) -> list[dict]:
        self.calls.append((query, params or {}))
        return self.next_rows


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.next_json: dict = {}

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._Resp(self.next_json)

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._Resp(self.next_json)


@pytest.fixture
def fake_clickhouse():
    fake = FakeClickHouse()
    state.clickhouse = fake
    yield fake
    state.clickhouse = None


@pytest.fixture
def fake_http_client():
    fake = FakeHttpClient()
    state.http_client = fake
    yield fake
    state.http_client = None


@pytest.fixture
def auth_settings(monkeypatch):
    """Deterministic operator credentials for tests that need to exercise
    the auth boundary, instead of whatever happens to be in the environment."""
    monkeypatch.setattr(settings, "api_gateway_operator_api_key", "test-operator-key")
    # >= 32 bytes: PyJWT 2.13+ warns below RFC 7518's recommended HMAC key
    # length for HS256 (see pip-audit's PYSEC-2025-183 finding) - keeping
    # test fixtures realistic avoids that warning without changing anything
    # about what's actually being tested.
    monkeypatch.setattr(settings, "api_gateway_jwt_secret", "test-jwt-signing-secret-that-is-long-enough")
    return settings


@pytest.fixture
def operator_token(auth_settings):
    from app.auth import issue_token

    return issue_token(auth_settings.api_gateway_operator_api_key).access_token


@pytest.fixture
def client(fake_clickhouse, fake_http_client, auth_settings):
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(alerts.router)
    app.include_router(models.router)
    app.include_router(system.router)
    return TestClient(app)
