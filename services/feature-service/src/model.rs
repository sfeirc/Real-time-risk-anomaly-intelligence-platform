//! Input: `RawEvent` (same shape as `services/ingestion/src/model.rs`, see
//! `docs/data-contracts.md`). Output: `FeatureEvent`
//! (`schemas/feature_event.schema.json`).

use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Domain {
    Market,
    Payments,
}

impl Domain {
    pub fn as_str(&self) -> &'static str {
        match self {
            Domain::Market => "market",
            Domain::Payments => "payments",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Channel {
    CardPresent,
    CardNotPresent,
    Wire,
    Ach,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Approved,
    Declined,
    Error,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MarketPayload {
    pub symbol: String,
    pub price: f64,
    pub size: f64,
    pub side: Side,
    pub bid: f64,
    pub ask: f64,
    pub exchange_latency_ms: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PaymentsPayload {
    pub txn_id: Uuid,
    pub merchant_id: String,
    pub account_id_hash: String,
    pub amount: f64,
    pub currency: String,
    pub channel: Channel,
    pub country: String,
    pub processing_latency_ms: f64,
    pub status: Status,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Payload {
    Market(MarketPayload),
    Payments(PaymentsPayload),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RawEvent {
    pub event_id: Uuid,
    pub domain: Domain,
    pub entity_key: String,
    pub source: String,
    pub seq: u64,
    pub ts_event: String,
    #[serde(default)]
    pub ts_ingest: Option<String>,
    #[serde(default)]
    pub corrupted: bool,
    #[serde(default)]
    pub scenario_label: Option<String>,
    pub payload: Payload,
}

/// Output — see `docs/data-contracts.md` section 2 / `schemas/feature_event.schema.json`.
/// Deliberately does NOT carry `scenario_label`/`corrupted`: nothing
/// downstream of this struct is allowed to see ground truth. See
/// `docs/metrics.md`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureEvent {
    pub entity_key: String,
    pub domain: Domain,
    pub window_start: String,
    pub window_end: String,
    pub window_size_s: f64,
    pub count: u64,
    pub throughput_eps: f64,

    pub latency_p50_ms: f64,
    pub latency_p99_ms: f64,
    pub error_rate: f64,

    // Explicit `null` (not an omitted key) for the domain that doesn't apply —
    // matches docs/data-contracts.md and lets ClickHouse's JSONEachRow insert
    // populate these Nullable columns unambiguously.
    pub vwap: Option<f64>,
    pub spread_bps: Option<f64>,
    pub realized_vol: Option<f64>,
    pub order_imbalance: Option<f64>,

    pub mean_amount: Option<f64>,
    pub sum_amount: Option<f64>,
    pub decline_rate: Option<f64>,
    pub distinct_accounts: Option<u64>,

    pub ewma_mean: f64,
    pub ewma_var: f64,
    pub zscore: f64,
    pub primary_metric: f64,
}
