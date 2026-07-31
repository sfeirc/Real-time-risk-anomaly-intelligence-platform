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

    # not 8080: a common enough dev-tool default to collide on a real
    # workstation that this project picks an out-of-the-way port instead.
    api_gateway_http_port: int = 8180

    data_generator_url: str = "http://data-generator:8765"

    # bounded history replayed to a client on WebSocket connect, so the
    # dashboard has something to render before the first live event arrives
    ws_backlog_size: int = 30

    # --- auth (see docs/roadmap.md "Auth: none -> everything") ---
    # Shared secret an operator exchanges at POST /auth/token for a
    # short-lived JWT. Read-only endpoints stay open (viewer access); only
    # control-plane actions (e.g. /api/scenarios/inject) require the token
    # this key buys — the RBAC boundary is "who can act", not "who can look".
    # Empty by default so a misconfigured deployment fails closed (nobody can
    # log in) rather than open.
    api_gateway_operator_api_key: str = ""
    # HMAC secret for signing issued JWTs. If left empty the service falls
    # back to a random secret generated at process start (logged loudly) so
    # local dev still works — but every issued token is invalidated on
    # restart, so set this explicitly anywhere that matters.
    api_gateway_jwt_secret: str = ""
    api_gateway_jwt_expiry_minutes: int = 60

    # Standard OTel env var name (not project-specific) - every OTel SDK in
    # every language reads this same variable; see app/telemetry.py.
    otel_exporter_otlp_endpoint: str = "http://jaeger:4318"


settings = Settings()
