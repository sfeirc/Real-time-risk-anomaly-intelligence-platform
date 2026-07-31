from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    kafka_brokers: str = "redpanda:9092"
    kafka_topic_alerts: str = "alerts"
    kafka_topic_model_metrics: str = "model-metrics"
    kafka_consumer_group: str = "api-gateway"

    clickhouse_host: str = "clickhouse"
    clickhouse_http_port: int = 8123
    clickhouse_db: str = "risk"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    api_gateway_http_port: int = 8080

    data_generator_url: str = "http://data-generator:8765"

    # bounded history replayed to a client on WebSocket connect, so the
    # dashboard has something to render before the first live event arrives
    ws_backlog_size: int = 30


settings = Settings()
