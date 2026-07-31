# Roadmap — what a real production version adds next

This project makes several deliberate scope decisions to stay buildable and
operable by one person in a reasonable amount of time. None of them are
accidents or oversights — each is called out where it's made, in code
comments and in `ARCHITECTURE.md`. This doc collects them in one place along
with what replaces each one at real multi-team production scale, so the
gap between "working demo" and "production system" is explicit rather than
implied.

## Data contracts: hand-maintained doc → schema registry

`docs/data-contracts.md` + `schemas/*.schema.json`, checked in CI
(`tests/integration/test_contracts.py`), catch contract drift between
services today. At real scale, with multiple teams independently deploying
producers/consumers, this becomes a **schema registry** (Confluent Schema
Registry, Avro/Protobuf with compatibility modes) so a breaking change is
rejected at publish time, not caught by a CI job that runs after the fact.

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

## Auth: none → everything

There is currently no authentication anywhere in this stack — not on the
API gateway, not on the dashboard, not between internal services. That's
appropriate for a project scoped to a single developer's local Docker
network; it is the single largest gap between this and anything
internet-facing. A real deployment needs, at minimum: API gateway auth
(even a shared bearer token beats nothing), mTLS or a service mesh between
internal hops, and RBAC on who can call `/api/scenarios/inject`-equivalent
control-plane endpoints in a system that can actually block things.
