# Roadmap — what a real production version adds next

This project makes several deliberate scope decisions to stay buildable and
operable by one person in a reasonable amount of time. None of them are
accidents or oversights — each is called out where it's made, in code
comments and in `ARCHITECTURE.md`. This doc collects them in one place along
with what replaces each one at real multi-team production scale, so the
gap between "working demo" and "production system" is explicit rather than
implied.

## Data contracts: hand-maintained doc → schema registry (done)

`schemas/*.schema.json` are now registered with Redpanda's schema registry
(`scripts/schema_registry.py`) under BACKWARD compatibility, enforced by a
dedicated CI job (`.github/workflows/ci.yml`'s `schema-registry`) that spins
up a real Redpanda container and proves the registry actually rejects a
breaking change and accepts a compatible one — not just that registration
succeeds. See `docs/data-contracts.md` for the subject list and the one
known gap (Redpanda's JSON Schema compatibility checker doesn't yet resolve
`$ref`, which degrades `raw-events-value`'s check to a warning).

What's still not here: the registry governs compatibility, but messages
stay plain JSON rather than the Confluent wire-format envelope (magic byte +
schema ID) real Avro/Protobuf deployments use — a deliberate call (see
`ARCHITECTURE.md`), not a gap, at this message size and team size. A real
multi-team deployment with many independent producer teams would likely
want the wire-format envelope too, so a consumer can decode against the
exact schema ID a message was written with instead of assuming "whatever's
currently registered."

## Windowing: processing-time → event-time with watermarks

`feature-service` buckets by wall-clock arrival time (see
`services/feature-service/src/window.rs`), not by `ts_event`. That's fine
with sub-5ms ingestion lag and no replay/reordering. A feed with real
network jitter, multi-region ingestion, or historical replay needs
event-time windowing with watermarks (a la Flink/Kafka Streams) so a
late-arriving event still lands in the correct window instead of the window
that happened to be open when it arrived.

## Kafka semantics: at-least-once → idempotent alert writes (done for alerts)

`feature-service` and `ml-inference` still use `enable.auto.commit` and
don't checkpoint in-memory window/detector state, so a crash mid-window
still reprocesses a few seconds of data on restart. What's changed:
`ml-inference` now derives `alert_id` deterministically from
`(domain, entity_key, window_end)` (`services/ml-inference/app/pipeline.py`)
instead of a random UUID, and `risk.alerts` is a `ReplacingMergeTree(ts)`
keyed on that same tuple plus `alert_id`
(`infra/clickhouse/init/01_schema.sql`) — so a reprocessed window produces
a row that collapses into the same one at merge time (or immediately under
`SELECT ... FINAL`), instead of a second, permanently-stored alert for the
same real-world event. Verified against a live ClickHouse: two inserts with
the same key produce 2 raw rows until `OPTIMIZE TABLE ... FINAL`, then 1
(keeping the later `ts`) — see the schema comment and the commit that added
this for the exact repro.

Not fully closed: this dedups the durable audit trail (`risk.alerts`), not
every downstream consumer. The materialized rollup views
(`alerts_rollup_5m`, `probable_cause_rollup_1h`) fire per insert and are
*not* re-deduplicated by a later merge on the base table, and the live `/ws`
feed relays whatever `ml-inference` produces to Kafka before any ClickHouse
merge happens — so a rare reprocessing event can still transiently
double-count in a dashboard chart or show one alert twice in the live feed
for a few seconds. `tests/eval/eval_lib.py`'s precision/recall is unaffected
(it counts *distinct alerted windows*, a set membership test, not alert
rows). A production fraud-*blocking* system - where a duplicate `block`
action has a real operational cost, not just a cosmetic one - would still
want Kafka transactions (`exactly_once_v2`) or a dedup check before acting,
not just before storing.

## Unsupervised models: periodic batch refit → true online learning

Isolation Forest and the autoencoder refit from scratch on a rolling buffer
every `retrain_every_n_windows` (see
`services/ml-inference/app/detectors/isolation_forest.py` /
`autoencoder.py`) — simple, but each refit is a CPU spike (see
`docs/metrics.md`'s note on bounded latency spikes) and the model has no
memory of anything outside the current buffer. Real streaming anomaly
detection uses incremental algorithms (e.g. streaming Isolation Forest
variants, online autoencoders with mini-batch SGD) that update continuously
without a stop-the-world refit.

## Model deployment: baked into the image → a model registry

`scripts/train_xgboost.py` writes straight to
`services/ml-inference/app/models/artifacts/`, picked up by
`XGBoostDetector.reload()` or a restart. Fine for one model, one owner.
Multiple models, versioned rollback, and A/B comparison need an actual model
registry (MLflow, or a bespoke ClickHouse-table-plus-S3 scheme) with
`model_version` (already in the alert schema) driving which artifact is
live, not "whichever file is on disk."

## Single-node Redpanda/ClickHouse → clustered

Both run single-node here — the point is the pipeline architecture, not
demonstrating Kafka/ClickHouse cluster operations. Production needs
Redpanda/Kafka replication (`replicas > 1`, `acks=all` already set) and
ClickHouse sharding/replication (`ReplicatedMergeTree`, distributed tables)
for the durability and availability guarantees a risk system actually needs.

## Observability: metrics/logs only → distributed tracing (done)

Every service now emits OpenTelemetry spans (`services/*/src/telemetry.rs`
for the Rust services, `services/*/app/telemetry.py` for the Python ones),
propagated across every Kafka hop via a W3C `traceparent` message header, to
Jaeger (`docker-compose.yml`'s `jaeger` service, UI on :16686). Before this,
`docs/metrics.md`'s latency numbers were aggregate histograms - "p99 is
174ms" told you the shape of the distribution but not why any *one* slow
alert was slow. Now `ingest_event → compute_window → score_window →
relay_to_ws` is one trace per (a representative sampling of) event, with
per-hop timing, viewable end to end in Jaeger for any single alert.

Two deliberate simplifications, not oversights: (1) `feature-service`
aggregates many raw events into one window, so it picks the *most recent*
contributing event's trace as the window's representative parent rather
than a full span-Link fan-in to every event that contributed - readable
traces over exhaustive ones, the same tradeoff real windowed-stream tracing
(Kafka Streams, Flink) usually makes. (2) tracing is best-effort everywhere:
an export failure or an unreachable Jaeger never affects whether an event
actually gets processed - no service treats it as a hard dependency (see
`docker-compose.yml`'s comment on the `jaeger` service).

## Rules engine: YAML file → owned, audited config service

`services/ml-inference/app/rules.yaml` is read at process start (plus
`RulesEngine.reload()` for a hot-reload path that nothing currently calls
automatically). A real compliance-owned rules engine needs an audit trail
of who changed which threshold when, a review/approval flow, and probably
its own small service rather than a file that ships with the container
image.

## Auth: none → gateway boundary done, internal mesh still open

The API gateway now has a real authenticated boundary: `POST /auth/token`
exchanges a shared operator API key for a short-lived HS256 JWT
(`services/api-gateway/app/auth.py`), and the one control-plane action this
API exposes — `POST /api/scenarios/inject` — requires it
(`services/api-gateway/app/routes/system.py`). Every read-only endpoint
(alerts, model metrics, throughput, the `/ws` stream) stays open on purpose:
the RBAC boundary is "who can act", not "who can look", which is also why
there's only one role (`operator`) rather than a viewer/operator split — a
viewer role would gate nothing that isn't already public. The dashboard's
"Inject" control gates itself behind an "Unlock" prompt for the same reason
(`services/dashboard/src/components/ScenarioControls.tsx`). See
`docs/runbook.md`'s "Authentication" section for the exact flow.

What this does *not* close: there's still no mTLS or service mesh between
internal hops (ingestion → Kafka → feature-service → ML → alerts all trust
the Docker network implicitly), no per-operator identity (one shared key,
not individual accounts — fine for a demo operator console, not for an
audit trail of who injected what), and no encryption in transit anywhere
(plaintext Kafka, plaintext HTTP). A real deployment needs those three on
top of what's here now.
