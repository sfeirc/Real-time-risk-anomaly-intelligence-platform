-- Rollup tables + materialized views so dashboard queries never scan the
-- full raw_events/alerts tables. Averages are NOT pre-aggregated (summing
-- an average across merged parts is wrong); store sum+count and divide at
-- query time instead — see services/api-gateway for the query helpers.

CREATE TABLE IF NOT EXISTS risk.alerts_rollup_5m
(
    bucket              DateTime,
    domain              LowCardinality(String),
    severity            LowCardinality(String),
    alert_count         UInt64,
    sum_anomaly_score   Float64,
    sum_latency_ms      Float64
)
ENGINE = SummingMergeTree((alert_count, sum_anomaly_score, sum_latency_ms))
ORDER BY (bucket, domain, severity);

CREATE MATERIALIZED VIEW IF NOT EXISTS risk.alerts_rollup_5m_mv
TO risk.alerts_rollup_5m
AS
SELECT
    toStartOfFiveMinutes(ts) AS bucket,
    domain,
    severity,
    count()                              AS alert_count,
    sum(anomaly_score)                   AS sum_anomaly_score,
    sum(latency_ingest_to_alert_ms)      AS sum_latency_ms
FROM risk.alerts
GROUP BY bucket, domain, severity;

CREATE TABLE IF NOT EXISTS risk.throughput_rollup_1m
(
    bucket      DateTime,
    domain      LowCardinality(String),
    entity_key  String,
    events      UInt64
)
ENGINE = SummingMergeTree(events)
ORDER BY (bucket, domain, entity_key);

CREATE MATERIALIZED VIEW IF NOT EXISTS risk.throughput_rollup_1m_mv
TO risk.throughput_rollup_1m
AS
SELECT
    toStartOfMinute(window_end) AS bucket,
    domain,
    entity_key,
    sum(count) AS events
FROM risk.features
GROUP BY bucket, domain, entity_key;

CREATE TABLE IF NOT EXISTS risk.probable_cause_rollup_1h
(
    bucket          DateTime,
    domain          LowCardinality(String),
    probable_cause  LowCardinality(String),
    alert_count     UInt64
)
ENGINE = SummingMergeTree(alert_count)
ORDER BY (bucket, domain, probable_cause);

CREATE MATERIALIZED VIEW IF NOT EXISTS risk.probable_cause_rollup_1h_mv
TO risk.probable_cause_rollup_1h
AS
SELECT
    toStartOfHour(ts) AS bucket,
    domain,
    probable_cause,
    count() AS alert_count
FROM risk.alerts
GROUP BY bucket, domain, probable_cause;
