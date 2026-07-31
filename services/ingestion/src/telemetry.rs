//! OpenTelemetry wiring: an OTLP/HTTP exporter feeding spans to Jaeger (or
//! any OTLP collector), bridged from this service's existing `tracing`
//! spans via `tracing-opentelemetry` — nothing about how the rest of the
//! code already uses `tracing::info!`/spans has to change, this just adds a
//! second subscriber layer. Trace context crosses the Kafka hop as a W3C
//! `traceparent` message header (see `inject_current_context`/
//! `extract_parent_context`), since Kafka has no built-in trace-context
//! carrier the way HTTP headers give you for free.

use std::collections::HashMap;

use opentelemetry::global;
use opentelemetry::propagation::Injector;
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
/// it and shut it down explicitly before exit — dropping it implicitly
/// would lose whatever spans are still batched but not yet exported.
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

/// Injects the *current* tracing span's OTel context as W3C `traceparent`
/// (+ `tracestate`) headers, to attach to an outgoing Kafka message. This
/// service only ever produces (it's the first hop in the pipeline), so it
/// has no matching `extract_parent_context` - see feature-service's copy of
/// this module for the consumer side of the same propagation.
pub fn inject_current_context() -> HashMap<String, String> {
    let mut carrier = HashMap::new();
    let cx = tracing::Span::current().context();
    global::get_text_map_propagator(|propagator| {
        propagator.inject_context(&cx, &mut HeaderCarrier(&mut carrier));
    });
    carrier
}
