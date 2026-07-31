//! Orchestrates the whole pipeline stage on a single async task:
//! Kafka-consume raw events into per-entity windows, sweep for windows past
//! their deadline, produce `FeatureEvent`s to Kafka, and batch-insert both
//! `features` and a flattened copy of every raw event into ClickHouse. A
//! single task (via `tokio::select!`) avoids putting the entity-window
//! `HashMap` behind a `Mutex` — at this event rate the bottleneck is I/O
//! (Kafka, ClickHouse HTTP), not CPU, so there's nothing to gain from
//! spreading windows across worker threads.
//!
//! Offset commits are `enable.auto.commit` (at-least-once): the in-memory
//! window state isn't checkpointed either, so manual commit-after-flush
//! wouldn't buy exactly-once semantics without also persisting window
//! state — not worth the complexity at this scale. A restart re-windows
//! from the last committed offset and simply produces a few
//! partially-overlapping windows on the resumed entities.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::message::{Header, Headers, Message, OwnedHeaders};
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::util::Timeout;
use tracing::Instrument;
use tracing_opentelemetry::OpenTelemetrySpanExt;

use crate::clickhouse::ClickHouseSink;
use crate::config::Config;
use crate::metrics::Metrics;
use crate::model::{Domain, FeatureEvent, RawEvent};
use crate::telemetry;
use crate::window::EntityWindow;

/// Reads a consumed Kafka message's headers into a plain map so they can
/// outlive the borrowed message (needed to stash the latest one per entity
/// across `handle_raw_event` calls, for `sweep_and_emit` to pick up later).
fn extract_headers(headers: Option<&rdkafka::message::BorrowedHeaders>) -> HashMap<String, String> {
    let mut map = HashMap::new();
    if let Some(headers) = headers {
        for header in headers.iter() {
            if let Some(value) = header.value
                && let Ok(value) = std::str::from_utf8(value)
            {
                map.insert(header.key.to_string(), value.to_string());
            }
        }
    }
    map
}

pub async fn run(cfg: &Config, metrics: Arc<Metrics>) {
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", &cfg.kafka_brokers)
        .set("group.id", &cfg.consumer_group)
        .set("auto.offset.reset", "latest")
        .set("enable.auto.commit", "true")
        .create()
        .expect("failed to create kafka consumer");
    consumer
        .subscribe(&[cfg.topic_raw_events.as_str()])
        .expect("failed to subscribe to raw-events topic");

    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", &cfg.kafka_brokers)
        .set("message.timeout.ms", "10000")
        .set("compression.type", "lz4")
        .set("linger.ms", "5")
        .create()
        .expect("failed to create kafka producer");

    let ch_sink = ClickHouseSink::new(
        cfg.clickhouse_base_url.clone(),
        cfg.clickhouse_db.clone(),
        cfg.clickhouse_user.clone(),
        cfg.clickhouse_password.clone(),
    );
    let mut features_batch: Vec<FeatureEvent> = Vec::new();
    let mut raw_events_batch: Vec<RawEvent> = Vec::new();

    let mut windows: HashMap<String, EntityWindow> = HashMap::new();
    // Latest incoming traceparent per entity, so the span for a window's
    // eventual flush (see sweep_and_emit) can be a child of *some* real
    // upstream trace instead of a disconnected root. A window aggregates
    // many raw events; picking the most recent one's context as the
    // representative parent is a deliberate simplification over a full
    // fan-in of span Links to every contributing event - readable traces
    // over exhaustive ones, same tradeoff windowed-stream tracing usually
    // makes elsewhere (Kafka Streams, Flink).
    let mut window_trace_headers: HashMap<String, HashMap<String, String>> = HashMap::new();

    // 25ms so window-close detection latency stays well under the ~20ms p99
    // budget in ARCHITECTURE.md; cheap at this entity count (a handful of
    // `should_flush` checks per tick, not a query).
    let mut sweep_interval = tokio::time::interval(Duration::from_millis(25));
    let mut ch_flush_interval = tokio::time::interval(Duration::from_millis(cfg.clickhouse_flush_interval_ms));

    tracing::info!(
        brokers = %cfg.kafka_brokers,
        topic_in = %cfg.topic_raw_events,
        topic_out = %cfg.topic_features,
        "feature-service consumer loop starting"
    );

    loop {
        tokio::select! {
            msg = consumer.recv() => {
                match msg {
                    Ok(m) => {
                        if let Some(payload) = m.payload() {
                            let headers = extract_headers(m.headers());
                            handle_raw_event(payload, cfg, &mut windows, &mut window_trace_headers, headers, &mut raw_events_batch, &metrics);
                        }
                    }
                    Err(e) => tracing::warn!(error = %e, "kafka consume error"),
                }
            }
            _ = sweep_interval.tick() => {
                sweep_and_emit(&mut windows, &mut window_trace_headers, &producer, cfg, &metrics, &mut features_batch).await;
                if features_batch.len() >= cfg.clickhouse_batch_size {
                    flush_features(&ch_sink, &mut features_batch, &metrics).await;
                }
                if raw_events_batch.len() >= cfg.clickhouse_batch_size {
                    flush_raw_events(&ch_sink, &mut raw_events_batch, &metrics).await;
                }
            }
            _ = ch_flush_interval.tick() => {
                flush_features(&ch_sink, &mut features_batch, &metrics).await;
                flush_raw_events(&ch_sink, &mut raw_events_batch, &metrics).await;
            }
        }
    }
}

fn handle_raw_event(
    payload: &[u8],
    cfg: &Config,
    windows: &mut HashMap<String, EntityWindow>,
    window_trace_headers: &mut HashMap<String, HashMap<String, String>>,
    trace_headers: HashMap<String, String>,
    raw_events_batch: &mut Vec<RawEvent>,
    metrics: &Metrics,
) {
    let event: RawEvent = match serde_json::from_slice(payload) {
        Ok(e) => e,
        Err(e) => {
            metrics.parse_errors_total.inc();
            tracing::warn!(error = %e, "failed to parse raw event");
            return;
        }
    };
    metrics.events_consumed_total.with_label_values(&[event.domain.as_str()]).inc();

    let window_size_s = match event.domain {
        Domain::Market => cfg.window_market_s,
        Domain::Payments => cfg.window_payments_s,
    };
    let window = windows
        .entry(event.entity_key.clone())
        .or_insert_with(|| EntityWindow::new(event.domain, window_size_s, cfg.ewma_alpha, Utc::now()));
    window.add(&event);
    window_trace_headers.insert(event.entity_key.clone(), trace_headers);

    raw_events_batch.push(event);
}

async fn sweep_and_emit(
    windows: &mut HashMap<String, EntityWindow>,
    window_trace_headers: &mut HashMap<String, HashMap<String, String>>,
    producer: &FutureProducer,
    cfg: &Config,
    metrics: &Metrics,
    ch_batch: &mut Vec<FeatureEvent>,
) {
    let now = Utc::now();
    for (entity_key, window) in windows.iter_mut() {
        if !window.should_flush(now) {
            continue;
        }
        let Some(mut feature) = window.flush() else { continue };
        feature.entity_key = entity_key.clone();

        if let Ok(window_end) = DateTime::parse_from_rfc3339(&feature.window_end) {
            let lag_ms = (now - window_end.with_timezone(&Utc)).num_milliseconds() as f64;
            metrics.window_emit_lag_ms.observe(lag_ms.max(0.0));
        }
        metrics.windows_emitted_total.with_label_values(&[feature.domain.as_str()]).inc();

        // Child of the most recent contributing raw event's trace (see the
        // doc comment on window_trace_headers in `run`), so this window's
        // whole trace - through ml-inference's eventual scoring - is
        // reachable from whichever WS event happened to seed it.
        let parent_cx = window_trace_headers
            .get(entity_key)
            .map(telemetry::extract_parent_context)
            .unwrap_or_default();
        let span = tracing::info_span!("compute_window", entity_key = %entity_key, domain = %feature.domain.as_str(), count = feature.count);
        // Best-effort like the rest of this service's telemetry (metrics,
        // logs): a tracing-plumbing failure here must never affect whether
        // the feature event itself gets produced.
        let _ = span.set_parent(parent_cx);

        async {
            match serde_json::to_vec(&feature) {
                Ok(payload) => {
                    let mut headers = OwnedHeaders::new();
                    for (key, value) in telemetry::inject_current_context() {
                        headers = headers.insert(Header { key: &key, value: Some(&value) });
                    }
                    let record = FutureRecord::to(&cfg.topic_features).payload(&payload).key(entity_key).headers(headers);
                    if let Err((e, _owned)) = producer.send(record, Timeout::After(Duration::from_secs(5))).await {
                        metrics.kafka_produce_errors_total.inc();
                        tracing::warn!(error = %e.to_string(), entity = %entity_key, "failed to produce feature event");
                    }
                }
                Err(e) => tracing::error!(error = %e, "failed to serialize feature event (should never happen)"),
            }
        }
        .instrument(span)
        .await;

        ch_batch.push(feature);
    }
}

async fn flush_features(sink: &ClickHouseSink, batch: &mut Vec<FeatureEvent>, metrics: &Metrics) {
    if batch.is_empty() {
        return;
    }
    let rows = std::mem::take(batch);
    let n = rows.len();
    match sink.insert_features(&rows).await {
        Ok(()) => metrics.clickhouse_rows_written_total.with_label_values(&["features"]).inc_by(n as u64),
        Err(e) => {
            metrics.clickhouse_write_errors_total.with_label_values(&["features"]).inc();
            tracing::warn!(error = %e, rows = n, "clickhouse features batch insert failed, dropping batch");
        }
    }
}

async fn flush_raw_events(sink: &ClickHouseSink, batch: &mut Vec<RawEvent>, metrics: &Metrics) {
    if batch.is_empty() {
        return;
    }
    let rows = std::mem::take(batch);
    let n = rows.len();
    match sink.insert_raw_events(&rows).await {
        Ok(()) => metrics.clickhouse_rows_written_total.with_label_values(&["raw_events"]).inc_by(n as u64),
        Err(e) => {
            metrics.clickhouse_write_errors_total.with_label_values(&["raw_events"]).inc();
            tracing::warn!(error = %e, rows = n, "clickhouse raw_events batch insert failed, dropping batch");
        }
    }
}
