mod clickhouse;
mod config;
mod consumer;
mod ewma;
mod http;
mod metrics;
mod model;
mod window;

use std::sync::Arc;

use config::Config;
use metrics::Metrics;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .json()
        .init();

    let cfg = Config::from_env();
    let metrics = Arc::new(Metrics::new());

    tracing::info!(
        window_market_s = cfg.window_market_s,
        window_payments_s = cfg.window_payments_s,
        ewma_alpha = cfg.ewma_alpha,
        metrics_port = cfg.metrics_port,
        "starting feature-service"
    );

    let http_metrics = metrics.clone();
    let metrics_port = cfg.metrics_port;
    tokio::spawn(async move {
        if let Err(e) = http::serve(metrics_port, http_metrics).await {
            tracing::error!(error = %e, "metrics server exited");
        }
    });

    consumer::run(&cfg, metrics).await;
}
