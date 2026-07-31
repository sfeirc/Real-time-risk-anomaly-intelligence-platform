from __future__ import annotations

import logging

from fastapi import APIRouter

from ..auth import TokenRequest, TokenResponse, issue_token

log = logging.getLogger("api-gateway.routes.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
async def login(req: TokenRequest) -> TokenResponse:
    return issue_token(req.api_key)
