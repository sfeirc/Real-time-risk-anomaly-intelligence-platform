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
`services/data-generator/app/config.py`). To force one on demand for a demo:

```bash
curl -X POST http://localhost:8765/inject \
  -H 'Content-Type: application/json' \
  -d '{"domain":"market","entity_key":"BTC-USD","scenario":"volatility_spike","duration_s":30}'
```

Valid `scenario` values: `volatility_spike`, `fraud_pattern`, `latency_incident`,
`data_corruption`, `regime_change`, `volume_spike`. See
`services/data-generator/app/scenarios.py` for exact parameters.
