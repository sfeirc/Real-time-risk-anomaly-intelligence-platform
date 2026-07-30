use std::env;

fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_or_parse<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

pub struct Config {
    pub kafka_brokers: String,
    pub topic_raw_events: String,
    pub topic_features: String,
    pub consumer_group: String,
    pub clickhouse_base_url: String,
    pub clickhouse_db: String,
    pub clickhouse_user: String,
    pub clickhouse_password: String,
    pub metrics_port: u16,
    pub window_market_s: f64,
    pub window_payments_s: f64,
    pub ewma_alpha: f64,
    pub clickhouse_batch_size: usize,
    pub clickhouse_flush_interval_ms: u64,
}

impl Config {
    pub fn from_env() -> Self {
        let ch_host = env_or("CLICKHOUSE_HOST", "clickhouse");
        let ch_port = env_or("CLICKHOUSE_HTTP_PORT", "8123");
        let ch_db = env_or("CLICKHOUSE_DB", "risk");
        Self {
            kafka_brokers: env_or("KAFKA_BROKERS", "redpanda:9092"),
            topic_raw_events: env_or("KAFKA_TOPIC_RAW_EVENTS", "raw-events"),
            topic_features: env_or("KAFKA_TOPIC_FEATURES", "features"),
            consumer_group: env_or("FEATURE_SERVICE_CONSUMER_GROUP", "feature-service"),
            clickhouse_base_url: format!("http://{ch_host}:{ch_port}"),
            clickhouse_db: ch_db,
            clickhouse_user: env_or("CLICKHOUSE_USER", "default"),
            clickhouse_password: env_or("CLICKHOUSE_PASSWORD", ""),
            metrics_port: env_or_parse("FEATURE_SERVICE_METRICS_PORT", 9102u16),
            window_market_s: env_or_parse("FEATURE_WINDOW_MARKET_S", 2.0),
            window_payments_s: env_or_parse("FEATURE_WINDOW_PAYMENTS_S", 5.0),
            ewma_alpha: env_or_parse("FEATURE_EWMA_ALPHA", 0.1),
            clickhouse_batch_size: env_or_parse("FEATURE_CLICKHOUSE_BATCH_SIZE", 200usize),
            clickhouse_flush_interval_ms: env_or_parse("FEATURE_CLICKHOUSE_FLUSH_INTERVAL_MS", 1000u64),
        }
    }
}
