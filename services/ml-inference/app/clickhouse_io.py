"""Batched ClickHouse HTTP sink for `alerts` and `model_metrics`. Same
batch-not-per-row rationale as `services/feature-service/src/clickhouse.rs`.

The Kafka `alerts` payload nests `explanation: {probable_cause,
top_features}` per docs/data-contracts.md; the `risk.alerts` table flattens
that into `probable_cause` + a `top_features` JSON string column (see
infra/clickhouse/init/01_schema.sql) — `_alert_row` does that translation so
one `AlertEvent` object still serves both destinations.
"""

from __future__ import annotations

import json

import httpx

from .schemas import AlertEvent, ModelMetricsEvent


def _alert_row(alert: AlertEvent) -> dict:
    return {
        "alert_id": str(alert.alert_id),
        "entity_key": alert.entity_key,
        "domain": alert.domain,
        "ts": alert.ts,
        "window_end": alert.window_end,
        "anomaly_score": alert.anomaly_score,
        "severity": alert.severity,
        "action": alert.action,
        "detectors": {k: v for k, v in alert.detectors.model_dump().items() if v is not None},
        "probable_cause": alert.explanation.probable_cause,
        "top_features": json.dumps([tf.model_dump() for tf in alert.explanation.top_features]),
        "model_version": alert.model_version,
        "drift_flag": alert.drift_flag,
        "latency_ingest_to_alert_ms": alert.latency_ingest_to_alert_ms,
    }


class ClickHouseSink:
    def __init__(self, base_url: str, database: str, user: str, password: str) -> None:
        self._base_url = base_url
        self._database = database
        self._auth = (user, password)
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _insert(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(row) for row in rows)
        resp = await self._client.post(
            f"{self._base_url}/",
            params={
                "database": self._database,
                "query": f"INSERT INTO {table} FORMAT JSONEachRow",
                "date_time_input_format": "best_effort",
            },
            content=body,
            auth=self._auth,
        )
        resp.raise_for_status()

    async def insert_alerts(self, alerts: list[AlertEvent]) -> None:
        await self._insert("alerts", [_alert_row(a) for a in alerts])

    async def insert_model_metrics(self, metrics_events: list[ModelMetricsEvent]) -> None:
        await self._insert("model_metrics", [m.model_dump() for m in metrics_events])
