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

## Kafka semantics: at-least-once → exactly-once (or idempotent by design)

`feature-service` and `ml-inference` use `enable.auto.commit` and don't
checkpoint in-memory window/detector state. A crash mid-window reprocesses
a few seconds of data on restart — harmless here since features/alerts
aren't billing events. A production fraud-blocking system needs either
Kafka transactions (`exactly_once_v2`) or an idempotent write path (e.g.
alert dedup keyed on `(entity_key, window_end)`), because "we alerted
twice" and "we blocked a legitimate transaction twice" are real costs, not
rounding errors.

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
