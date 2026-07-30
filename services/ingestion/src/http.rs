use std::sync::Arc;

use axum::{routing::get, Router};

use crate::metrics::Metrics;

async fn metrics_handler(metrics: axum::extract::State<Arc<Metrics>>) -> String {
    metrics.encode()
}

async fn health_handler() -> &'static str {
    "ok"
}

pub async fn serve(port: u16, metrics: Arc<Metrics>) -> std::io::Result<()> {
    let app = Router::new()
        .route("/metrics", get(metrics_handler))
        .route("/health", get(health_handler))
        .with_state(metrics);
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    tracing::info!(port, "metrics server listening");
    axum::serve(listener, app).await
}
