from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

registry = CollectorRegistry()
# A custom CollectorRegistry (rather than prometheus_client's global default)
# does NOT get process_cpu_seconds_total / process_resident_memory_bytes for
# free — those only auto-register on the default registry at import time.
# Registered explicitly here so "Cout memoire / CPU" (see docs/metrics.md) is
# an actual exposed metric, not just a line in a doc nobody wired up.
ProcessCollector(registry=registry)
PlatformCollector(registry=registry)

features_consumed_total = Counter(
    "ml_features_consumed_total", "Feature events consumed from Kafka", ["domain"], registry=registry
)
alerts_produced_total = Counter(
    "ml_alerts_produced_total", "Alert events produced", ["domain", "severity"], registry=registry
)
parse_errors_total = Counter("ml_parse_errors_total", "Feature events that failed to parse", registry=registry)
processing_errors_total = Counter(
    "ml_processing_errors_total", "pipeline.process raised for a well-formed feature event", ["domain"], registry=registry
)
kafka_produce_errors_total = Counter("ml_kafka_produce_errors_total", "Kafka produce failures", registry=registry)
clickhouse_write_errors_total = Counter("ml_clickhouse_write_errors_total", "Failed ClickHouse batch inserts", registry=registry)
clickhouse_rows_written_total = Counter("ml_clickhouse_rows_written_total", "Rows written to ClickHouse", ["table"], registry=registry)

inference_ms = Histogram(
    "ml_inference_ms",
    "Time from feature-event consume to alert produce (ensemble scoring + explanation)",
    buckets=[1, 2, 5, 10, 20, 50, 100, 250, 500, 1000],
    registry=registry,
)

model_ready = Gauge("ml_model_ready", "1 if a domain's unsupervised models have completed their first fit", ["domain", "model"], registry=registry)
drift_detected_gauge = Gauge("ml_drift_detected", "1 if drift is currently flagged for a domain", ["domain"], registry=registry)
background_task_alive = Gauge(
    "ml_background_task_alive", "1 if a supervised background task is still running; 0 if it exited (should never happen)", ["task"], registry=registry
)


def render() -> bytes:
    return generate_latest(registry)
