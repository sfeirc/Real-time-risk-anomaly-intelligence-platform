from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import metrics, state
from ..config import settings

log = logging.getLogger("api-gateway.system")
router = APIRouter(prefix="/api", tags=["system"])


@router.get("/throughput")
async def throughput(since_minutes: int = Query(30, ge=1, le=1440)):
    """1-minute bucketed event counts per entity, from risk.throughput_rollup_1m
    (see infra/clickhouse/init/02_views.sql) — the dashboard's events/sec chart."""
    query = (
        "SELECT bucket, domain, entity_key, events FROM throughput_rollup_1m "
        "WHERE bucket > now() - INTERVAL {since_minutes:UInt32} MINUTE "
        "ORDER BY bucket ASC"
    )
    try:
        return await state.clickhouse.query_rows(query, {"since_minutes": since_minutes})
    except httpx.HTTPStatusError as e:
        metrics.clickhouse_query_errors_total.labels(endpoint="throughput").inc()
        log.warning("throughput query failed: %s", e)
        raise HTTPException(status_code=502, detail="clickhouse query failed") from e


@router.get("/entities")
async def entities():
    """Proxies data-generator's entity registry so the dashboard only ever
    talks to this gateway, never directly to internal services."""
    try:
        resp = await state.http_client.get(f"{settings.data_generator_url}/entities")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        log.warning("data-generator /entities proxy failed: %s", e)
        raise HTTPException(status_code=502, detail="data-generator unreachable") from e


@router.get("/scenarios")
async def active_scenarios():
    try:
        resp = await state.http_client.get(f"{settings.data_generator_url}/scenarios")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        log.warning("data-generator /scenarios proxy failed: %s", e)
        raise HTTPException(status_code=502, detail="data-generator unreachable") from e


class InjectRequest(BaseModel):
    domain: str
    entity_key: str
    scenario: str
    duration_s: float | None = None


@router.post("/scenarios/inject")
async def inject_scenario(req: InjectRequest):
    """Lets the dashboard trigger a demo scenario without exposing
    data-generator's port directly — see docs/runbook.md."""
    try:
        resp = await state.http_client.post(f"{settings.data_generator_url}/inject", json=req.model_dump())
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        log.warning("data-generator /inject proxy failed: %s", e)
        raise HTTPException(status_code=502, detail="data-generator unreachable") from e
