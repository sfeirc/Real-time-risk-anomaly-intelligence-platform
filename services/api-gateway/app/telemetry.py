"""OpenTelemetry wiring: an OTLP/HTTP exporter feeding spans to Jaeger (or
any OTLP collector). Trace context crosses each Kafka hop as a W3C
traceparent message header (see extract_parent_context) since Kafka has no
built-in trace-context carrier the way HTTP headers give you for free — the
same manual propagation pattern the Rust services use (see
services/ingestion/src/telemetry.rs). api-gateway is the last hop (it only
relays alerts to WebSocket clients, never produces further onto Kafka), so
unlike ml-inference's copy of this module it has no matching
inject_current_context."""

from __future__ import annotations

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def init(service_name: str, otlp_endpoint: str) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    return provider


def extract_parent_context(headers: list[tuple[str, bytes]] | None) -> Context:
    """Extracts a parent context from a consumed Kafka message's headers, so
    a new span created here becomes a child of whatever produced it."""
    carrier = {k: v.decode("utf-8") for k, v in (headers or []) if v is not None}
    return propagate.extract(carrier)
