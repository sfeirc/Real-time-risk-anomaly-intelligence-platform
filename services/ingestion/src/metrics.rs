use prometheus::{
    Encoder, Histogram, HistogramOpts, IntCounter, IntCounterVec, IntGauge, Opts, Registry,
    TextEncoder,
};

pub struct Metrics {
    pub registry: Registry,
    pub events_total: IntCounterVec,
    pub parse_errors_total: IntCounter,
    pub kafka_errors_total: IntCounter,
    pub ws_reconnects_total: IntCounter,
    pub ws_connected: IntGauge,
    pub inflight_sends: IntGauge,
    pub ws_to_kafka_ms: Histogram,
}

impl Metrics {
    pub fn new() -> Self {
        let registry = Registry::new();

        let events_total = IntCounterVec::new(
            Opts::new("ingestion_events_total", "Total raw events ingested"),
            &["domain"],
        )
        .unwrap();
        let parse_errors_total = IntCounter::new(
            "ingestion_parse_errors_total",
            "Events that failed to parse or validate",
        )
        .unwrap();
        let kafka_errors_total = IntCounter::new(
            "ingestion_kafka_produce_errors_total",
            "Kafka produce failures",
        )
        .unwrap();
        let ws_reconnects_total = IntCounter::new(
            "ingestion_ws_reconnects_total",
            "Websocket (re)connect attempts",
        )
        .unwrap();
        let ws_connected = IntGauge::new(
            "ingestion_ws_connected",
            "1 if the websocket source is currently connected",
        )
        .unwrap();
        let inflight_sends = IntGauge::new(
            "ingestion_inflight_sends",
            "Number of produce calls currently in flight (backpressure indicator)",
        )
        .unwrap();
        let ws_to_kafka_ms = Histogram::with_opts(
            HistogramOpts::new(
                "ingestion_ws_to_kafka_ms",
                "Time from frame receipt to Kafka produce ack",
            )
            .buckets(vec![
                0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 5000.0,
            ]),
        )
        .unwrap();

        registry.register(Box::new(events_total.clone())).unwrap();
        registry
            .register(Box::new(parse_errors_total.clone()))
            .unwrap();
        registry
            .register(Box::new(kafka_errors_total.clone()))
            .unwrap();
        registry
            .register(Box::new(ws_reconnects_total.clone()))
            .unwrap();
        registry.register(Box::new(ws_connected.clone())).unwrap();
        registry.register(Box::new(inflight_sends.clone())).unwrap();
        registry.register(Box::new(ws_to_kafka_ms.clone())).unwrap();

        Self {
            registry,
            events_total,
            parse_errors_total,
            kafka_errors_total,
            ws_reconnects_total,
            ws_connected,
            inflight_sends,
            ws_to_kafka_ms,
        }
    }

    pub fn encode(&self) -> String {
        let mut buf = Vec::new();
        TextEncoder::new()
            .encode(&self.registry.gather(), &mut buf)
            .expect("prometheus text encoding never fails for valid metrics");
        String::from_utf8(buf).expect("prometheus text encoder always emits valid utf8")
    }
}

impl Default for Metrics {
    fn default() -> Self {
        Self::new()
    }
}
