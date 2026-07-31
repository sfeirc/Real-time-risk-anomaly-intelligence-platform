from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app import telemetry


def test_inject_then_extract_round_trips_the_same_trace_id():
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("root"):
        headers = telemetry.inject_current_context()
        current_trace_id = trace.get_current_span().get_span_context().trace_id

    assert any(k == "traceparent" for k, _ in headers)

    cx = telemetry.extract_parent_context(headers)
    extracted_span_context = trace.get_current_span(cx).get_span_context()
    assert extracted_span_context.trace_id == current_trace_id


def test_extract_of_empty_headers_yields_no_valid_span_context():
    cx = telemetry.extract_parent_context(None)
    assert not trace.get_current_span(cx).get_span_context().is_valid
