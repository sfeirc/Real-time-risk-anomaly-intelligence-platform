import time

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import JWT_ALGORITHM, require_operator


@pytest.fixture
def protected_client(auth_settings):
    app = FastAPI()

    @app.get("/protected")
    async def protected(_principal: dict = Depends(require_operator)):
        return {"ok": True}

    return TestClient(app)


def test_token_issued_for_correct_api_key(client, auth_settings):
    resp = client.post("/auth/token", json={"api_key": auth_settings.api_gateway_operator_api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "operator"
    assert body["expires_in"] == auth_settings.api_gateway_jwt_expiry_minutes * 60
    assert body["access_token"]


def test_token_rejected_for_wrong_api_key(client, auth_settings):
    resp = client.post("/auth/token", json={"api_key": "wrong-key"})
    assert resp.status_code == 401


def test_token_rejected_when_operator_key_unset(client, auth_settings, monkeypatch):
    monkeypatch.setattr(auth_settings, "api_gateway_operator_api_key", "")
    resp = client.post("/auth/token", json={"api_key": ""})
    assert resp.status_code == 401


def test_protected_endpoint_requires_token(protected_client):
    resp = protected_client.get("/protected")
    assert resp.status_code == 401


def test_protected_endpoint_accepts_valid_token(protected_client, operator_token):
    resp = protected_client.get("/protected", headers={"Authorization": f"Bearer {operator_token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_protected_endpoint_rejects_expired_token(protected_client, auth_settings):
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "operator", "role": "operator", "iat": now - 7200, "exp": now - 3600},
        auth_settings.api_gateway_jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    resp = protected_client.get("/protected", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_protected_endpoint_rejects_wrong_role(protected_client, auth_settings):
    now = int(time.time())
    viewer_token = jwt.encode(
        {"sub": "someone", "role": "viewer", "iat": now, "exp": now + 3600},
        auth_settings.api_gateway_jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    resp = protected_client.get("/protected", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 403


def test_protected_endpoint_rejects_token_signed_with_wrong_secret(protected_client):
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "operator", "role": "operator", "iat": now, "exp": now + 3600},
        "not-the-real-secret-but-still-long-enough-to-avoid-a-warning",
        algorithm=JWT_ALGORITHM,
    )
    resp = protected_client.get("/protected", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
