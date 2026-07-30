mod backoff;
mod config;
mod http;
mod ingest;
mod metrics;
mod model;
mod sink;

use std::sync::Arc;

use config::Config;
use metrics::Metrics;
use sink::KafkaSink;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .json()
        .init();

    let cfg = Config::from_env();
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
