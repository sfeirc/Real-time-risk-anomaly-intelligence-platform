# Data Contracts

Canonical event schemas shared by every service. Rust (`serde`), Python (`pydantic`)
and TypeScript types in each service are hand-kept in sync with this document;
JSON Schema copies live under `schemas/` and are enforced two ways:

1. **`tests/integration/test_contracts.py`** validates real (or example)
   payloads against `schemas/*.schema.json` in CI, catching drift between
   what a service actually emits and what it claims to emit.
2. **A real schema registry** (`scripts/schema_registry.py`, against
   Redpanda's Confluent-compatible schema registry — already provisioned in
   `docker-compose.yml`, port 18081) registers each schema as a subject and
   enforces **BACKWARD compatibility**: a breaking change to `schemas/*.schema.json`
   is rejected at registration time, not just caught by a CI job after the
   fact. `make schema-check` runs the same dry-run check locally before you
   push. Subjects follow Confluent's `{topic}-value` naming:

   | Subject | Schema file |
   |---|---|
   | `raw-events-value` | `schemas/raw_event.schema.json` |
   | `features-value` | `schemas/feature_event.schema.json` |
   | `alerts-value` | `schemas/alert_event.schema.json` |
   | `model-metrics-value` | `schemas/model_metrics_event.schema.json` |

   Messages on the wire stay plain JSON — deliberately not wrapped in the
   Confluent magic-byte + schema-ID envelope Avro/Protobuf setups typically
   use, so `rpk topic consume` stays human-readable. The registry's value
   here (reject a breaking change before it ships) doesn't require that
   envelope, only the registration and compatibility-check API calls the
   script makes. See `ARCHITECTURE.md`'s rationale table and
   `scripts/schema_registry.py`'s docstring for the one known gap: Redpanda's
   JSON Schema compatibility checker doesn't yet resolve `$ref`/`definitions`
   (affects `raw-events-value` only, which uses `$ref` for its market/payments
   payload variants — registration still works, the dry-run compatibility
   check degrades to a warning for that one subject).

Two synthetic domains are simulated end-to-end so the platform reads as
relevant to both quant/market-risk and fintech/fraud audiences:

- `market`  — crypto trade ticks (spread, volatility, microstructure)
- `payments` — card/wire/ACH transactions (fraud, latency, decline patterns)

Kafka topics (Redpanda), 6 partitions each unless noted, keyed by `entity_key`
so all events for one symbol/merchant land on the same partition and preserve
order for stateful windowing:

| Topic             | Producer         | Consumer                     | Key           |
|--------------------|------------------|-------------------------------|---------------|
| `raw-events`        | ingestion        | feature-service                | `entity_key`  |
| `features`           | feature-service  | ml-inference                   | `entity_key`  |
| `alerts`              | ml-inference     | api-gateway                    | `entity_key`  |
| `model-metrics`        | ml-inference     | api-gateway                    | `model_id`    |

## 1. Raw event envelope (`raw-events`)

Emitted by `data-generator`, re-stamped with `ts_ingest` by `ingestion` on
receipt (this delta is `latency_ingestion_ms`, the first leg of the headline
"ingestion → alert" latency metric).

```jsonc
{
  "event_id": "uuid",                // generator-assigned
  "domain": "market" | "payments",
  "entity_key": "string",            // symbol (market) or merchant_id (payments) — Kafka partition key
  "source": "string",                // synthetic exchange / PSP name
  "seq": "uint64",                   // per-entity monotonic sequence, used to detect gaps/reordering
  "ts_event": "RFC3339",             // origin timestamp (set by generator)
  "ts_ingest": "RFC3339 | null",     // set by ingestion service on receipt; null until then
  "corrupted": "bool",               // generator-injected corruption flag (ground truth for eval)
  "scenario_label": "string | null", // generator-injected anomaly scenario ground truth, null in normal operation
  "payload": "MarketPayload | PaymentsPayload"
}
```

### MarketPayload

```jsonc
{
  "symbol": "string",       // e.g. "BTC-USD"
  "price": "float64",
  "size": "float64",
  "side": "buy" | "sell",
  "bid": "float64",
  "ask": "float64",
  "exchange_latency_ms": "float64"
}
```

### PaymentsPayload

```jsonc
{
  "txn_id": "uuid",
  "merchant_id": "string",
  "account_id_hash": "string",   // sha256, never a raw PAN/account number
  "amount": "float64",
  "currency": "ISO4217 string",
  "channel": "card_present" | "card_not_present" | "wire" | "ach",
  "country": "ISO3166-1 alpha2",
  "processing_latency_ms": "float64",
  "status": "approved" | "declined" | "error"
}
```

## 2. Feature event (`features`)

Emitted by `feature-service` once per entity per tumbling window
(`window_size_s`, default 2s market / 5s payments). Carries every input the
downstream detectors need — no service re-reads `raw-events`.

```jsonc
{
  "entity_key": "string",
  "domain": "market" | "payments",
  "window_start": "RFC3339",         // wall-clock time this window opened; see latency note below
  "window_end": "RFC3339",
  "window_size_s": "float64",
  "count": "uint64",
  "throughput_eps": "float64",       // count / window_size_s

  // shared
  "latency_p50_ms": "float64",
  "latency_p99_ms": "float64",
  "error_rate": "float64",           // status=="error"/"declined" (payments) or corrupted-flag rate (market)

  // market-only (null for payments)
  "vwap": "float64 | null",
  "spread_bps": "float64 | null",
  "realized_vol": "float64 | null",  // stdev of log returns within window, annualized
  "order_imbalance": "float64 | null",

  // payments-only (null for market)
  "mean_amount": "float64 | null",
  "sum_amount": "float64 | null",
  "decline_rate": "float64 | null",
  "distinct_accounts": "uint64 | null",

  // rolling statistical state (both domains)
  "ewma_mean": "float64",
  "ewma_var": "float64",
  "zscore": "float64",               // (primary_metric - ewma_mean) / sqrt(ewma_var)
  "primary_metric": "float64"        // realized_vol (market) or mean_amount (payments) — what zscore/EWMA track
}
```

`primary_metric` exists so the statistical detectors have one well-defined
signal per domain instead of guessing which of a dozen fields matters.

`window_start` is what the headline `latency_ingest_to_alert_ms` metric is
measured from (see below) — `ml-inference` never sees individual raw events,
only windowed features, so "ingestion to alert" is defined as window-open to
alert-emit rather than first-tick to alert-emit. That slightly overstates
true detection latency (by up to one `window_size_s`) but is exact and
always available, instead of approximate and sometimes not.

## 3. Alert event (`alerts`)

Emitted by `ml-inference` for any window whose ensemble anomaly score crosses
the `watch` threshold (see `docs/metrics.md` for thresholds).

`alert_id` is a deterministic UUID5 of `(domain, entity_key, window_end)`
(`services/ml-inference/app/pipeline.py`), not random - reprocessing the
same window after a crash (see docs/roadmap.md "Kafka semantics") produces
the *same* `alert_id`, which is what lets `risk.alerts`
(`ReplacingMergeTree`, see `infra/clickhouse/init/01_schema.sql`) collapse
a reprocessed window down to one stored alert instead of a duplicate.

```jsonc
{
  "alert_id": "uuid",
  "entity_key": "string",
  "domain": "market" | "payments",
  "ts": "RFC3339",
  "window_end": "RFC3339",           // features window this alert was computed from
  "anomaly_score": "float64",        // 0..1 ensemble score
  "severity": "watch" | "alert" | "critical",
  "action": "watch" | "alert" | "block",
  "detectors": {
    "zscore": "float64",
    "ewma": "float64",
    "cusum": "float64",
    "isolation_forest": "float64",
    "autoencoder": "float64",
    "xgboost": "float64 | null"      // null until a labeled model is trained/loaded
  },
  "explanation": {
    "probable_cause": "volatility_spike" | "latency_incident" | "fraud_pattern"
                     | "data_corruption" | "regime_change" | "volume_spike" | "unknown",
    "top_features": [
      // `contribution` is signed when XGBoost is loaded (real SHAP values,
      // see app/detectors/xgboost_detector.py::shap_contributions -
      // positive pushes toward anomalous, negative pushes toward normal),
      // and non-negative (a magnitude-only deviation-from-baseline
      // heuristic) as a fallback before a model exists - see app/explain.py.
      { "feature": "string", "value": "float64", "baseline": "float64", "contribution": "float64" }
    ]
  },
  "model_version": "string",
  "drift_flag": "bool",
  "latency_ingest_to_alert_ms": "float64"  // ts - feature.window_start, the headline latency metric
}
```

## 4. Model/drift metrics event (`model-metrics`)

Emitted periodically (default every 30s) by `ml-inference`.

```jsonc
{
  "model_id": "string",
  "model_version": "string",
  "ts": "RFC3339",
  "eval_window_s": "float64",
  "precision": "float64 | null",
  "recall": "float64 | null",
  "f1": "float64 | null",
  "false_positive_rate": "float64 | null",
  "psi_by_feature": { "feature_name": "float64" },   // Population Stability Index vs training baseline
  "ks_stat_by_feature": { "feature_name": "float64" },
  "drift_detected": "bool",
  "events_scored": "uint64",
  "throughput_eps": "float64",
  "p50_inference_ms": "float64",
  "p99_inference_ms": "float64"
}
```

## Severity → action mapping (rules engine defaults)

| anomaly_score | severity   | default action |
|----------------|-----------|-----------------|
| < 0.55           | (no alert) | —                |
| 0.55 – 0.75        | `watch`    | `watch`           |
| 0.75 – 0.90         | `alert`    | `alert`           |
| ≥ 0.90                | `critical` | `block`           |

Thresholds and the score→action mapping are configurable per-domain in
`services/ml-inference/app/rules.yaml` — see `docs/metrics.md`.
