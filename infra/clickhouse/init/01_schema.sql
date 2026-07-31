-- Real-time risk & anomaly intelligence platform — ClickHouse schema.
-- Applied automatically on first container start via /docker-entrypoint-initdb.d.
-- Mirrors docs/data-contracts.md. Wide, denormalized tables by design: this is
-- an analytics sink, not an OLTP store — joins are avoided on the hot query path.

CREATE DATABASE IF NOT EXISTS risk;

-- ---------------------------------------------------------------------------
-- raw_events: every ingested tick, both domains in one table (domain-specific
-- columns are Nullable and unused for the other domain). Short TTL: this is
-- the highest-cardinality, lowest-value-per-row table — features/alerts are
-- the durable record of "what happened", raw_events is for replay/debugging.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk.raw_events
(
    event_id            UUID,
    domain              LowCardinality(String),
    entity_key          String,
    source              LowCardinality(String),
    seq                 UInt64,
    ts_event            DateTime64(3, 'UTC'),
    ts_ingest           DateTime64(3, 'UTC'),
    corrupted           Bool,
    scenario_label      LowCardinality(String) DEFAULT '',

    -- market payload
    m_symbol            LowCardinality(String) DEFAULT '',
    m_price             Nullable(Float64),
    m_size              Nullable(Float64),
    m_side              LowCardinality(String) DEFAULT '',
    m_bid               Nullable(Float64),
    m_ask               Nullable(Float64),
    m_exchange_latency_ms Nullable(Float64),

    -- payments payload
    p_txn_id            Nullable(UUID),
    p_merchant_id       LowCardinality(String) DEFAULT '',
    p_account_id_hash   String DEFAULT '',
    p_amount            Nullable(Float64),
    p_currency          LowCardinality(String) DEFAULT '',
    p_channel           LowCardinality(String) DEFAULT '',
    p_country           LowCardinality(String) DEFAULT '',
    p_processing_latency_ms Nullable(Float64),
    p_status            LowCardinality(String) DEFAULT '',

    ingest_date         Date MATERIALIZED toDate(ts_ingest)
)
ENGINE = MergeTree
PARTITION BY ingest_date
ORDER BY (domain, entity_key, ts_event)
TTL ingest_date + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- features: one row per entity per window, output of feature-service.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk.features
(
    entity_key          String,
    domain              LowCardinality(String),
    window_start         DateTime64(3, 'UTC'),
    window_end          DateTime64(3, 'UTC'),
    window_size_s        Float64,
    count               UInt64,
    throughput_eps        Float64,

    latency_p50_ms        Float64,
    latency_p99_ms        Float64,
    error_rate           Float64,

    vwap                Nullable(Float64),
    spread_bps           Nullable(Float64),
    realized_vol          Nullable(Float64),
    order_imbalance        Nullable(Float64),

    mean_amount           Nullable(Float64),
    sum_amount            Nullable(Float64),
    decline_rate          Nullable(Float64),
    distinct_accounts      Nullable(UInt64),

    ewma_mean            Float64,
    ewma_var             Float64,
    zscore              Float64,
    primary_metric         Float64,

    ingest_date          Date MATERIALIZED toDate(window_end)
)
ENGINE = MergeTree
PARTITION BY ingest_date
ORDER BY (domain, entity_key, window_end)
TTL ingest_date + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- alerts: durable audit trail. Kept much longer than raw_events/features —
-- this is the table compliance/risk actually cares about historically.
--
-- ReplacingMergeTree, not plain MergeTree: ml-inference's Kafka consumer
-- uses `enable.auto.commit` and doesn't checkpoint in-memory state (see
-- docs/roadmap.md "Kafka semantics"), so a crash between scoring a window
-- and the next offset commit reprocesses that window on restart.
-- ml-inference derives `alert_id` deterministically from
-- (domain, entity_key, window_end) specifically so a reprocessed window
-- produces a row with the *same* sort key here, and ReplacingMergeTree(ts)
-- keeps only the highest-`ts` row per key at merge time - one alert per
-- window survives, not one per processing attempt.
--
-- Known limitation, not hidden: ReplacingMergeTree dedups at background
-- merge time (or with an explicit `FINAL`/`OPTIMIZE ... FINAL`), not at
-- insert time - a query between a duplicate insert and the next merge can
-- still briefly see both rows, and the materialized rollup views in
-- 02_views.sql fire per-insert and are *not* re-deduplicated by a later
-- merge on this table. Acceptable here because reprocessing only happens
-- across an actual crash/restart (rare, not steady-state), and exact counts
-- that need a guarantee (e.g. tests/eval's precision/recall) should query
-- with FINAL - see docs/data-contracts.md.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk.alerts
(
    alert_id            UUID,
    entity_key          String,
    domain              LowCardinality(String),
    ts                  DateTime64(3, 'UTC'),
    window_end          DateTime64(3, 'UTC'),
    anomaly_score         Float64,
    severity             LowCardinality(String),
    action              LowCardinality(String),

    detectors            Map(LowCardinality(String), Float64),

    probable_cause         LowCardinality(String),
    top_features          String,  -- JSON array, see schemas/alert_event.schema.json

    model_version          LowCardinality(String),
    drift_flag            Bool,
    latency_ingest_to_alert_ms Float64,

    ingest_date          Date MATERIALIZED toDate(ts)
)
ENGINE = ReplacingMergeTree(ts)
PARTITION BY ingest_date
ORDER BY (domain, entity_key, window_end, alert_id)
TTL ingest_date + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- model_metrics: periodic model quality + drift snapshots.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk.model_metrics
(
    model_id            LowCardinality(String),
    model_version         LowCardinality(String),
    ts                  DateTime64(3, 'UTC'),
    eval_window_s         Float64,

    precision            Nullable(Float64),
    recall              Nullable(Float64),
    f1                  Nullable(Float64),
    false_positive_rate     Nullable(Float64),

    psi_by_feature         Map(LowCardinality(String), Float64),
    ks_stat_by_feature      Map(LowCardinality(String), Float64),
    drift_detected         Bool,

    events_scored          UInt64,
    throughput_eps         Float64,
    p50_inference_ms        Float64,
    p99_inference_ms        Float64,

    ingest_date          Date MATERIALIZED toDate(ts)
)
ENGINE = MergeTree
PARTITION BY ingest_date
ORDER BY (model_id, ts)
TTL ingest_date + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;
