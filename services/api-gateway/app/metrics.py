from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

registry = CollectorRegistry()

alerts_relayed_total = Counter("api_alerts_relayed_total", "Alert events relayed from Kafka to WebSocket clients", registry=registry)
model_metrics_relayed_total = Counter("api_model_metrics_relayed_total", "Model-metrics events relayed from Kafka to WebSocket clients", registry=registry)
kafka_consume_errors_total = Counter("api_kafka_consume_errors_total", "Kafka consumer errors", ["topic"], registry=registry)
clickhouse_query_errors_total = Counter("api_clickhouse_query_errors_total", "Failed ClickHouse queries", ["endpoint"], registry=registry)
ws_clients_connected = Gauge("api_ws_clients_connected", "Currently connected WebSocket clients", registry=registry)


def render() -> bytes:
    return generate_latest(registry)
