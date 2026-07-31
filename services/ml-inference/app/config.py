from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    kafka_brokers: str = "redpanda:9092"
    kafka_topic_features: str = "features"
    kafka_topic_alerts: str = "alerts"
    kafka_topic_model_metrics: str = "model-metrics"
    kafka_consumer_group: str = "ml-inference"

    clickhouse_host: str = "clickhouse"
    clickhouse_http_port: int = 8123
    clickhouse_db: str = "risk"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""

    # not 8000/8080: common dev-tool defaults that collide often enough on a
    # real workstation that this project picks out-of-the-way ones instead.
    ml_inference_http_port: int = 8010

    anomaly_watch_threshold: float = 0.55
    anomaly_alert_threshold: float = 0.75
    anomaly_critical_threshold: float = 0.90

    drift_check_interval_s: float = 30.0
    model_metrics_interval_s: float = 30.0

    # rolling buffer of recent feature windows used to (re)fit the
    # unsupervised models; assumed mostly-normal (anomalies are a small
    # minority of traffic), which is the standard operating assumption for
    # this class of unsupervised detector.
    buffer_size: int = 500
    min_buffer_for_training: int = 60
    retrain_every_n_windows: int = 50

    isolation_forest_contamination: float = 0.05
    isolation_forest_n_estimators: int = 100

    autoencoder_hidden_dim: int = 8
    autoencoder_latent_dim: int = 3
    autoencoder_epochs: int = 60
    autoencoder_lr: float = 0.01

    cusum_slack_k: float = 0.5
    cusum_threshold_h: float = 5.0

    xgboost_model_path: str = "app/models/artifacts/xgboost_{domain}.json"

    rules_path: str = "app/rules.yaml"

    clickhouse_batch_size: int = 100
    clickhouse_flush_interval_s: float = 1.0

    model_version: str = "v0.1.0"


settings = Settings()
