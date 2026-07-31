from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from .. import metrics, state

log = logging.getLogger("api-gateway.models")
router = APIRouter(prefix="/api/model-metrics", tags=["model-metrics"])


@router.get("/latest")
async def latest_model_metrics():
    """Most recent row per `model_id` — what the dashboard's model-health
    panel polls on load before the WebSocket stream takes over."""
    query = (
        "SELECT model_id, model_version, ts, precision, recall, f1, false_positive_rate, "
        "psi_by_feature, ks_stat_by_feature, drift_detected, events_scored, throughput_eps, "
        "p50_inference_ms, p99_inference_ms "
        "FROM model_metrics "
        "ORDER BY ts DESC LIMIT 1 BY model_id"
    )
    try:
        return await state.clickhouse.query_rows(query)
    except httpx.HTTPStatusError as e:
        metrics.clickhouse_query_errors_total.labels(endpoint="latest_model_metrics").inc()
        log.warning("latest_model_metrics query failed: %s", e)
        raise HTTPException(status_code=502, detail="clickhouse query failed") from e


@router.get("/history")
async def model_metrics_history(model_id: str, since_hours: int = Query(24, ge=1, le=720)):
    query = (
        "SELECT ts, drift_detected, events_scored, throughput_eps, p50_inference_ms, p99_inference_ms "
        "FROM model_metrics "
        "WHERE model_id = {model_id:String} AND ts > now() - INTERVAL {since_hours:UInt32} HOUR "
        "ORDER BY ts ASC"
    )
    try:
        return await state.clickhouse.query_rows(query, {"model_id": model_id, "since_hours": since_hours})
    except httpx.HTTPStatusError as e:
        metrics.clickhouse_query_errors_total.labels(endpoint="model_metrics_history").inc()
        log.warning("model_metrics_history query failed: %s", e)
        raise HTTPException(status_code=502, detail="clickhouse query failed") from e
