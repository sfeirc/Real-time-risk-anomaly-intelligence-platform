"""OpenTelemetry wiring: an OTLP/HTTP exporter feeding spans to Jaeger (or
any OTLP collector). Trace context crosses each Kafka hop as a W3C
traceparent message header (see inject_current_context/
extract_parent_context) since Kafka has no built-in trace-context carrier
the way HTTP headers give you for free — the same manual propagation
pattern the Rust services use (see services/ingestion/src/telemetry.rs)."""

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


def inject_current_context() -> list[tuple[str, bytes]]:
    """Injects the current span's context as W3C traceparent (+ tracestate)
    headers, formatted as Kafka message headers (aiokafka wants
    `list[tuple[str, bytes]]`, not a dict)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(k, v.encode("utf-8")) for k, v in carrier.items()]


def extract_parent_context(headers: list[tuple[str, bytes]] | None) -> Context:
    """Extracts a parent context from a consumed Kafka message's headers, so
    a new span created here becomes a child of whatever produced it — the
    other half of inject_current_context."""
    carrier = {k: v.decode("utf-8") for k, v in (headers or []) if v is not None}
    return propagate.extract(carrier)
