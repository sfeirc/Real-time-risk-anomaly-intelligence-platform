from opentelemetry import trace

from app import telemetry


def test_extract_parses_a_real_w3c_traceparent_header():
    # api-gateway is the last hop (see app/telemetry.py's docstring), so it
    # only ever extracts - this is exactly the wire format Kafka headers
    # carry from ml-inference's inject_current_context, without needing a
    # running exporter/collector to produce one.
    headers = [("traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")]
    cx = telemetry.extract_parent_context(headers)
    span_context = trace.get_current_span(cx).get_span_context()
    assert span_context.is_valid
    assert format(span_context.trace_id, "032x") == "0af7651916cd43dd8448eb211c80319c"


def test_extract_of_missing_headers_yields_no_valid_span_context():
    cx = telemetry.extract_parent_context(None)
    assert not trace.get_current_span(cx).get_span_context().is_valid
