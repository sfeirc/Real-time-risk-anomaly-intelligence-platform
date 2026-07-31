mod clickhouse;
mod config;
mod consumer;
mod ewma;
mod http;
mod metrics;
mod model;
mod telemetry;
mod window;

use std::sync::Arc;

use config::Config;
use metrics::Metrics;

#[tokio::main]
async fn main() {
    let cfg = Config::from_env();
    // Kept alive for the process lifetime (never read otherwise): dropping
    // the provider is what flushes/shuts down the batch span exporter, and
    // consumer::run below never returns.
    let _tracer_provider = telemetry::init("feature-service", &cfg.otlp_endpoint);
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
