from __future__ import annotations

import logging
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import settings

log = logging.getLogger("api-gateway.auth")

JWT_ALGORITHM = "HS256"
OPERATOR_ROLE = "operator"

_bearer_scheme = HTTPBearer(auto_error=False)

if not settings.api_gateway_jwt_secret:
    # Dev convenience, not a production fallback: a real deployment must set
    # API_GATEWAY_JWT_SECRET explicitly. Falling back to an empty/constant
    # secret here would let anyone forge a valid operator token.
    settings.api_gateway_jwt_secret = secrets.token_urlsafe(32)
    log.warning(
        "API_GATEWAY_JWT_SECRET not set - using a random ephemeral secret "
        "for this process. Every issued token becomes invalid on restart. "
        "Set it explicitly for anything beyond local dev."
    )

if not settings.api_gateway_operator_api_key:
    # Fails closed (nobody can log in - see issue_token) rather than open,
    # but a config typo here is otherwise invisible until someone notices
    # every login attempt returning 401. Log it loudly at startup instead.
    log.warning(
        "API_GATEWAY_OPERATOR_API_KEY not set - POST /auth/token will reject "
        "every request until it is, so /api/scenarios/inject is unreachable."
    )


class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


def issue_token(api_key: str) -> TokenResponse:
    # `not settings.api_gateway_operator_api_key` first: an unset key must reject every
    # request, including one that (accidentally or not) also sends an empty
    # string - otherwise an unconfigured deployment fails *open*.
    if not settings.api_gateway_operator_api_key or not secrets.compare_digest(api_key, settings.api_gateway_operator_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    now = int(time.time())
    expires_in = settings.api_gateway_jwt_expiry_minutes * 60
    payload = {"sub": OPERATOR_ROLE, "role": OPERATOR_ROLE, "iat": now, "exp": now + expires_in}
    token = jwt.encode(payload, settings.api_gateway_jwt_secret, algorithm=JWT_ALGORITHM)
    return TokenResponse(access_token=token, expires_in=expires_in, role=OPERATOR_ROLE)


async def require_operator(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """FastAPI dependency gating control-plane endpoints. Read-only routes
    never depend on this - only actions with a real-world side effect do."""
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(creds.credentials, settings.api_gateway_jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from e
    if payload.get("role") != OPERATOR_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operator role required")
    return payload
