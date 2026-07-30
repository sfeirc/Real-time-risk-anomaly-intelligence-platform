use std::env;

fn env_or(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_or_parse<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

pub struct Config {
    pub ws_url: String,
    pub kafka_brokers: String,
    pub kafka_topic_raw_events: String,
    pub metrics_port: u16,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            ws_url: env_or("INGESTION_WS_URL", "ws://data-generator:8765/stream"),
            kafka_brokers: env_or("KAFKA_BROKERS", "redpanda:9092"),
            kafka_topic_raw_events: env_or("KAFKA_TOPIC_RAW_EVENTS", "raw-events"),
            metrics_port: env_or_parse("INGESTION_METRICS_PORT", 9101u16),
        }
    }
}
