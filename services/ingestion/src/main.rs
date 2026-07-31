mod backoff;
mod config;
mod http;
mod ingest;
mod metrics;
mod model;
mod sink;
mod telemetry;

use std::sync::Arc;

use config::Config;
use metrics::Metrics;
use sink::KafkaSink;

#[tokio::main]
async fn main() {
    let cfg = Config::from_env();
    // Kept alive for the process lifetime (never read otherwise): dropping
    // the provider is what flushes/shuts down the batch span exporter, and
    // `ingest::run` below never returns - see its own doc comment on why
    // there's no graceful-shutdown path in this service at all.
    let _tracer_provider = telemetry::init("ingestion", &cfg.otlp_endpoint);
    let metrics = Arc::new(Metrics::new());

    let sink = KafkaSink::new(&cfg.kafka_brokers, &cfg.kafka_topic_raw_events)
        .expect("failed to construct kafka producer");

    tracing::info!(
        ws_url = %cfg.ws_url,
        brokers = %cfg.kafka_brokers,
        topic = %cfg.kafka_topic_raw_events,
        metrics_port = cfg.metrics_port,
        "starting ingestion service"
    );

    let http_metrics = metrics.clone();
    let metrics_port = cfg.metrics_port;
    tokio::spawn(async move {
        if let Err(e) = http::serve(metrics_port, http_metrics).await {
            tracing::error!(error = %e, "metrics server exited");
        }
    });

    ingest::run(&cfg.ws_url, &sink, &metrics).await;
}
