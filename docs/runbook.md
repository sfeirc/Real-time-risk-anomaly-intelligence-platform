# Runbook — local deployment

## Prerequisites

- Docker + Docker Compose v2
- Rust 1.75+ (only needed to build/test outside Docker)
- Python 3.11+ (only needed to build/test outside Docker)
- Node 20+ (only needed to build/test outside Docker)

## Quickstart

```bash
cp .env.example .env
make infra-up      # Redpanda + ClickHouse + Prometheus + Grafana
make up             # full stack (builds + starts every service)
```

Endpoints once everything is healthy:

| Service | URL |
|---|---|
| Dashboard | http://localhost:5173 |
| API gateway | http://localhost:8180 (REST), ws://localhost:8180/ws |
| ML inference (direct) | http://localhost:8010 |
| Redpanda Console (topic browser) | http://localhost:8090 |
| ClickHouse HTTP | http://localhost:8123 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / `GRAFANA_ADMIN_PASSWORD` in `.env`) |
| Jaeger (distributed tracing) | http://localhost:16686 |

## Common operations

```bash
make ps                 # service status
make logs                # tail all logs
make down                 # stop everything, keep volumes
make clean                 # stop + delete volumes (full reset)

make test                   # rust + python + js test suites
make lint                    # clippy + ruff + eslint

make eval                     # precision/recall/drift report -> docs/benchmarks/latest.json
make load-test                 # throughput/latency benchmark -> docs/benchmarks/latest.json
make demo                       # scripted end-to-end demo (see scripts/demo.sh)
```

## Inspecting data directly

```bash
# Kafka topics
docker exec -it redpanda rpk topic list
docker exec -it redpanda rpk topic consume alerts --num 5

# ClickHouse
docker exec -it clickhouse clickhouse-client --query \
  "SELECT ts, entity_key, severity, anomaly_score, probable_cause FROM risk.alerts ORDER BY ts DESC LIMIT 20"
```

To wipe data mid-session without a full `make clean` (which drops volumes and
re-triggers the ClickHouse init scripts), truncate the **rollup tables too**,
not just the base ones — `alerts_rollup_5m` / `throughput_rollup_1m` /
`probable_cause_rollup_1h` are materialized views that accumulate on every
insert into their source table, so truncating `risk.alerts` alone leaves the
dashboard's charts and KPI tiles (which read the rollups, not `risk.alerts`
directly — see `services/api-gateway/app/routes/alerts.py`) still showing
every alert since the container started:

```bash
for t in raw_events features alerts model_metrics alerts_rollup_5m throughput_rollup_1m probable_cause_rollup_1h; do
  docker exec clickhouse clickhouse-client --query "TRUNCATE TABLE risk.$t"
done
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `redpanda-topics-init` exits non-zero | Redpanda not yet healthy when topic creation ran | `docker compose up -d redpanda-topics-init` again — it's idempotent (`\|\| true` on each `rpk topic create`) |
| `ingestion` crash-loops | `data-generator` not up yet / WS URL wrong | check `INGESTION_WS_URL` in `.env` matches the compose service name (`data-generator`, not `localhost`) |
| No alerts appear on dashboard | `ml-inference` still warming up the baseline (first ~30s of EWMA/IsolationForest warm-up window are intentionally quiet — see `docs/metrics.md`) | wait, or check `ml-inference` logs for `warmup_complete` |
| ClickHouse init scripts didn't run | volume already existed from a previous run | `make clean` then `make infra-up` (init scripts only run on an empty data dir) |
| Grafana panels empty | Prometheus scrape target down, or ClickHouse plugin not installed yet | check http://localhost:9090/targets ; Grafana installs `grafana-clickhouse-datasource` on first boot, can take ~30s |

## Injecting anomaly scenarios manually

`data-generator` injects scenarios automatically — `DATA_GENERATOR_SCENARIO_PROBABILITY`
(default 0.002) is a per-entity-per-second spawn chance, not a fraction of
windows; with scenarios lasting ~10-90s, that works out to each entity
being anomalous roughly 7% of the time (see the comment on `Settings` in
`services/data-generator/app/config.py`). To force one on demand for a demo,
either call `data-generator` directly (no auth, dev-only shortcut):

```bash
curl -X POST http://localhost:8765/inject \
  -H 'Content-Type: application/json' \
  -d '{"domain":"market","entity_key":"BTC-USD","scenario":"volatility_spike","duration_s":30}'
```

or go through the gateway the same way the dashboard's "Inject" button does,
which requires an operator JWT (see "Authentication" below):

```bash
TOKEN=$(curl -s -X POST http://localhost:8180/auth/token \
  -H 'Content-Type: application/json' \
  -d "{\"api_key\":\"$API_GATEWAY_OPERATOR_API_KEY\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -X POST http://localhost:8180/api/scenarios/inject \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"domain":"market","entity_key":"BTC-USD","scenario":"volatility_spike","duration_s":30}'
```

Valid `scenario` values: `volatility_spike`, `fraud_pattern`, `latency_incident`,
`data_corruption`, `regime_change`, `volume_spike`. See
`services/data-generator/app/scenarios.py` for exact parameters.

## Authentication

`api-gateway` has one authenticated boundary: `POST /api/scenarios/inject`,
the one control-plane endpoint this API exposes (see
`docs/roadmap.md` "Auth: none → everything"). Everything else — alerts,
model metrics, throughput, the `/ws` stream — is read-only telemetry and
stays open, on purpose: the RBAC boundary here is "who can act", not "who
can look".

```bash
curl -X POST http://localhost:8180/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"<API_GATEWAY_OPERATOR_API_KEY from .env>"}'
# -> {"access_token": "<jwt>", "token_type": "bearer", "expires_in": 3600, "role": "operator"}
```

Send that token as `Authorization: Bearer <jwt>` on `/api/scenarios/inject`.
The dashboard does this itself via a small "Unlock" prompt in the scenario
panel — enter the operator key once, it's cached in `localStorage` until the
token expires (`API_GATEWAY_JWT_EXPIRY_MINUTES`, default 60 minutes).

If `API_GATEWAY_OPERATOR_API_KEY` is unset, `/auth/token` rejects every
request (fails closed, not open) and `api-gateway` logs a warning at
startup. If `API_GATEWAY_JWT_SECRET` is unset, the service still runs — it
generates a random secret at startup (logged loudly) — but every issued
token is invalidated on restart, so set it explicitly beyond local dev.

## Tracing

Every service exports OpenTelemetry spans to Jaeger
(http://localhost:16686), propagated across every Kafka hop via a W3C
`traceparent` message header — one trace per event, from WebSocket ingest
through feature windowing and ML scoring to the alert reaching a dashboard
client. To see one: open Jaeger, pick "api-gateway" as the service and
"relay_to_ws" as the operation (that's the *last* hop, so searching there
finds complete traces rather than in-flight ones), and open any result. You
should see four spans: `ingestion: ingest_event` → `feature-service:
compute_window` → `ml-inference: score_window` → `api-gateway: relay_to_ws`.

Tracing is best-effort everywhere: if Jaeger is down or unreachable, spans
are silently dropped and nothing else is affected - no service treats it as
a dependency. `OTEL_EXPORTER_OTLP_ENDPOINT` (standard OTel env var,
defaults to `http://jaeger:4318`) points every exporter at it; see
`docs/roadmap.md`'s "Observability" entry for what this covers and its two
deliberate simplifications.
