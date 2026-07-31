from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from .. import metrics, state

log = logging.getLogger("api-gateway.alerts")
router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_LIST_COLUMNS = (
    "alert_id, entity_key, domain, ts, window_end, anomaly_score, severity, action, "
    "detectors, probable_cause, top_features, model_version, drift_flag, latency_ingest_to_alert_ms"
)


@router.get("")
async def list_alerts(
    domain: str | None = Query(None, pattern="^(market|payments)$"),
    severity: str | None = Query(None, pattern="^(watch|alert|critical)$"),
    entity_key: str | None = None,
    since_minutes: int = Query(60, ge=1, le=10_080),
    limit: int = Query(100, ge=1, le=1000),
):
    clauses = ["ts > now() - INTERVAL {since_minutes:UInt32} MINUTE"]
    params: dict = {"since_minutes": since_minutes, "limit": limit}
    if domain:
        clauses.append("domain = {domain:String}")
        params["domain"] = domain
    if severity:
        clauses.append("severity = {severity:String}")
        params["severity"] = severity
    if entity_key:
        clauses.append("entity_key = {entity_key:String}")
        params["entity_key"] = entity_key

    query = (
        f"SELECT {_LIST_COLUMNS} FROM alerts "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY ts DESC LIMIT {limit:UInt32}"
    )
    try:
        return await state.clickhouse.query_rows(query, params)
    except httpx.HTTPStatusError as e:
        metrics.clickhouse_query_errors_total.labels(endpoint="list_alerts").inc()
        log.warning("list_alerts query failed: %s", e)
        raise HTTPException(status_code=502, detail="clickhouse query failed") from e


@router.get("/rollup")
async def alerts_rollup(since_hours: int = Query(6, ge=1, le=168)):
    """5-minute bucketed counts + avg score/latency, for the dashboard's
    time-series charts — pre-aggregated in ClickHouse (risk.alerts_rollup_5m,
    see infra/clickhouse/init/02_views.sql) so this never scans raw alerts."""
    query = (
        "SELECT bucket, domain, severity, alert_count, "
        "sum_anomaly_score / alert_count AS avg_anomaly_score, "
        "sum_latency_ms / alert_count AS avg_latency_ms "
        "FROM alerts_rollup_5m "
        "WHERE bucket > now() - INTERVAL {since_hours:UInt32} HOUR "
        "ORDER BY bucket ASC"
    )
    try:
        return await state.clickhouse.query_rows(query, {"since_hours": since_hours})
    except httpx.HTTPStatusError as e:
        metrics.clickhouse_query_errors_total.labels(endpoint="alerts_rollup").inc()
        log.warning("alerts_rollup query failed: %s", e)
        raise HTTPException(status_code=502, detail="clickhouse query failed") from e


@router.get("/causes")
async def probable_causes(since_hours: int = Query(6, ge=1, le=168)):
    """Probable-cause breakdown for the dashboard's "why are things firing"
    panel, from the hourly rollup (risk.probable_cause_rollup_1h)."""
    query = (
        "SELECT domain, probable_cause, sum(alert_count) AS alert_count "
        "FROM probable_cause_rollup_1h "
        "WHERE bucket > now() - INTERVAL {since_hours:UInt32} HOUR "
        "GROUP BY domain, probable_cause "
        "ORDER BY alert_count DESC"
    )
    try:
        return await state.clickhouse.query_rows(query, {"since_hours": since_hours})
    except httpx.HTTPStatusError as e:
        metrics.clickhouse_query_errors_total.labels(endpoint="probable_causes").inc()
        log.warning("probable_causes query failed: %s", e)
        raise HTTPException(status_code=502, detail="clickhouse query failed") from e
