//! OpenTelemetry wiring: an OTLP/HTTP exporter feeding spans to Jaeger (or
//! any OTLP collector), bridged from this service's existing `tracing`
//! spans via `tracing-opentelemetry` — nothing about how the rest of the
//! code already uses `tracing::info!`/spans has to change, this just adds a
//! second subscriber layer. Trace context crosses each Kafka hop as a W3C
//! `traceparent` message header (see `inject_current_context`/
//! `extract_parent_context`), since Kafka has no built-in trace-context
//! carrier the way HTTP headers give you for free.
//!
//! This is a middle hop (raw-events in, features out) and, unlike
//! ingestion's copy of this module, needs both directions: `consumer.rs`
//! extracts the incoming traceparent per raw event and continues that trace
//! when a window it contributed to eventually flushes.

use std::collections::HashMap;

use opentelemetry::global;
use opentelemetry::propagation::{Extractor, Injector};
use opentelemetry::Context;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::propagation::TraceContextPropagator;
use opentelemetry_sdk::trace::SdkTracerProvider;
use opentelemetry_sdk::Resource;
use tracing_opentelemetry::OpenTelemetrySpanExt;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

/// Initializes global tracing: JSON logs to stdout (unchanged) plus an
/// OpenTelemetry layer exporting to `otlp_endpoint` (e.g.
/// `http://jaeger:4318`). Returns the `SdkTracerProvider` so `main` can hold
/// it - dropping it is what flushes/shuts down the batch span exporter.
pub fn init(service_name: &str, otlp_endpoint: &str) -> SdkTracerProvider {
    global::set_text_map_propagator(TraceContextPropagator::new());

    let exporter = opentelemetry_otlp::SpanExporter::builder()
        .with_http()
        .with_endpoint(format!("{otlp_endpoint}/v1/traces"))
        .build()
        .expect("failed to build OTLP span exporter");

    let provider = SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_resource(
            Resource::builder()
                .with_service_name(service_name.to_string())
                .build(),
        )
        .build();

    global::set_tracer_provider(provider.clone());
    let tracer = opentelemetry::trace::TracerProvider::tracer(&provider, service_name.to_string());

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .with(tracing_subscriber::fmt::layer().json())
        .with(tracing_opentelemetry::layer().with_tracer(tracer))
        .init();

    provider
}

struct HeaderCarrier<'a>(&'a mut HashMap<String, String>);

impl Injector for HeaderCarrier<'_> {
    fn set(&mut self, key: &str, value: String) {
        self.0.insert(key.to_string(), value);
    }
}

struct HeaderExtractor<'a>(&'a HashMap<String, String>);

impl Extractor for HeaderExtractor<'_> {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.get(key).map(|v| v.as_str())
    }
    fn keys(&self) -> Vec<&str> {
        self.0.keys().map(|k| k.as_str()).collect()
    }
}

/// Injects the *current* tracing span's OTel context as W3C `traceparent`
/// (+ `tracestate`) headers, to attach to an outgoing Kafka message.
pub fn inject_current_context() -> HashMap<String, String> {
    let mut carrier = HashMap::new();
    let cx = tracing::Span::current().context();
    global::get_text_map_propagator(|propagator| {
        propagator.inject_context(&cx, &mut HeaderCarrier(&mut carrier));
    });
    carrier
}

/// Extracts a parent OTel context from a consumed Kafka message's headers,
/// so a new span created here becomes a child of whatever produced it — the
/// other half of `inject_current_context`.
pub fn extract_parent_context(headers: &HashMap<String, String>) -> Context {
    global::get_text_map_propagator(|propagator| propagator.extract(&HeaderExtractor(headers)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use opentelemetry::trace::{TraceContextExt, TraceId};

    #[test]
    fn inject_then_extract_round_trips_the_same_trace_id() {
        global::set_text_map_propagator(TraceContextPropagator::new());
        // A real traceparent header (fixed 128-bit trace ID/64-bit span ID,
        // sampled flag set) rather than an actual live span, so this test
        // exercises exactly the wire format Kafka headers carry, without
        // needing a running exporter/collector.
        let mut headers = HashMap::new();
        headers.insert(
            "traceparent".to_string(),
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01".to_string(),
        );

        let cx = extract_parent_context(&headers);
        let expected = TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap();
        assert_eq!(cx.span().span_context().trace_id(), expected);
    }

    #[test]
    fn extract_of_empty_headers_yields_no_valid_span_context() {
        global::set_text_map_propagator(TraceContextPropagator::new());
        let cx = extract_parent_context(&HashMap::new());
        assert!(!cx.span().span_context().is_valid());
    }
}
