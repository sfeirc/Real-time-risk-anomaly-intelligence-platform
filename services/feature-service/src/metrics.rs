use prometheus::{Encoder, Histogram, HistogramOpts, IntCounter, IntCounterVec, Opts, Registry, TextEncoder};

pub struct Metrics {
    pub registry: Registry,
    pub events_consumed_total: IntCounterVec,
    pub windows_emitted_total: IntCounterVec,
    pub parse_errors_total: IntCounter,
    pub kafka_produce_errors_total: IntCounter,
    pub clickhouse_write_errors_total: IntCounterVec,
    pub clickhouse_rows_written_total: IntCounterVec,
    pub window_emit_lag_ms: Histogram,
}

impl Metrics {
    pub fn new() -> Self {
        let registry = Registry::new();

        let events_consumed_total = IntCounterVec::new(
            Opts::new("feature_events_consumed_total", "Raw events consumed from Kafka"),
            &["domain"],
        )
        .unwrap();
        let windows_emitted_total = IntCounterVec::new(
            Opts::new("feature_windows_emitted_total", "Feature windows emitted"),
            &["domain"],
        )
        .unwrap();
        let parse_errors_total =
            IntCounter::new("feature_parse_errors_total", "Raw events that failed to parse").unwrap();
        let kafka_produce_errors_total =
            IntCounter::new("feature_kafka_produce_errors_total", "Kafka produce failures on the features topic").unwrap();
        let clickhouse_write_errors_total = IntCounterVec::new(
            Opts::new("feature_clickhouse_write_errors_total", "Failed ClickHouse batch inserts"),
            &["table"],
        )
        .unwrap();
        let clickhouse_rows_written_total = IntCounterVec::new(
            Opts::new("feature_clickhouse_rows_written_total", "Rows successfully written to ClickHouse"),
            &["table"],
        )
        .unwrap();
        let window_emit_lag_ms = Histogram::with_opts(
            HistogramOpts::new("feature_window_emit_lag_ms", "now - window_end at the moment a feature event is produced")
                .buckets(vec![1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 250.0, 500.0, 1000.0]),
        )
        .unwrap();

        registry.register(Box::new(events_consumed_total.clone())).unwrap();
        registry.register(Box::new(windows_emitted_total.clone())).unwrap();
        registry.register(Box::new(parse_errors_total.clone())).unwrap();
        registry.register(Box::new(kafka_produce_errors_total.clone())).unwrap();
        registry.register(Box::new(clickhouse_write_errors_total.clone())).unwrap();
        registry.register(Box::new(clickhouse_rows_written_total.clone())).unwrap();
        registry.register(Box::new(window_emit_lag_ms.clone())).unwrap();

        Self {
            registry,
            events_consumed_total,
            windows_emitted_total,
            parse_errors_total,
            kafka_produce_errors_total,
            clickhouse_write_errors_total,
            clickhouse_rows_written_total,
            window_emit_lag_ms,
        }
    }

    pub fn encode(&self) -> String {
        let mut buf = Vec::new();
        TextEncoder::new().encode(&self.registry.gather(), &mut buf).expect("prometheus text encoding never fails for valid metrics");
        String::from_utf8(buf).expect("prometheus text encoder always emits valid utf8")
    }
}

impl Default for Metrics {
    fn default() -> Self {
        Self::new()
    }
}
