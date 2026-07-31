from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from . import metrics, state, telemetry
from .clickhouse_client import ClickHouseClient
from .config import settings
from .kafka_bridge import relay_topic
from .routes import alerts, auth, models, system

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("api-gateway")

telemetry.init("api-gateway", settings.otel_exporter_otlp_endpoint)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.clickhouse = ClickHouseClient(
        f"http://{settings.clickhouse_host}:{settings.clickhouse_http_port}",
        settings.clickhouse_db,
        settings.clickhouse_user,
        settings.clickhouse_password,
    )
    state.http_client = httpx.AsyncClient(timeout=10.0)

    tasks = [
        asyncio.create_task(
            relay_topic(
                settings.kafka_brokers, settings.kafka_topic_alerts, f"{settings.kafka_consumer_group}-alerts",
                "alert", state.ws_manager, metrics.alerts_relayed_total,
            )
        ),
        asyncio.create_task(
            relay_topic(
                settings.kafka_brokers, settings.kafka_topic_model_metrics, f"{settings.kafka_consumer_group}-model-metrics",
                "model_metrics", state.ws_manager, metrics.model_metrics_relayed_total,
            )
        ),
    ]
    log.info("api-gateway started: brokers=%s", settings.kafka_brokers)

    yield

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await state.clickhouse.close()
    await state.http_client.aclose()


app = FastAPI(title="real-time-risk api-gateway", lifespan=lifespan)
# Wide open deliberately, not an oversight: there is no authentication
# anywhere in this stack (see docs/roadmap.md's "Auth: none -> everything"),
# so scoping CORS tightly here would protect nothing real while breaking the
# dashboard whenever it's opened from a different host/port than whatever
# origin got hardcoded. The dashboard build (VITE_API_BASE_URL) calls this
# API cross-origin in production (unlike `npm run dev`, which proxies
# same-origin through Vite) — without this, every REST call from a built
# dashboard silently fails as a browser-side CORS block, not a 4xx/5xx.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
FastAPIInstrumentor.instrument_app(app)
app.include_router(auth.router)
app.include_router(alerts.router)
app.include_router(models.router)
app.include_router(system.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ws_clients": state.ws_manager.client_count}


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await state.ws_manager.connect(ws)
    metrics.ws_clients_connected.set(state.ws_manager.client_count)
    try:
        backlog_query = (
            "SELECT alert_id, entity_key, domain, ts, window_end, anomaly_score, severity, action, "
            "detectors, probable_cause, top_features, model_version, drift_flag, latency_ingest_to_alert_ms "
            "FROM alerts ORDER BY ts DESC LIMIT {n:UInt32}"
        )
        try:
            backlog = await state.clickhouse.query_rows(backlog_query, {"n": settings.ws_backlog_size})
            await ws.send_json({"type": "backlog", "data": list(reversed(backlog))})
        except httpx.HTTPStatusError as e:
            log.warning("failed to load ws backlog: %s", e)

        while True:
            # clients aren't expected to send anything; reading lets us
            # detect disconnects promptly instead of via a failed broadcast.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_manager.disconnect(ws)
        metrics.ws_clients_connected.set(state.ws_manager.client_count)
