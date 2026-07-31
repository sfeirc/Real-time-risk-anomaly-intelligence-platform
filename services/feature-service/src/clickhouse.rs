//! Batched ClickHouse sink over the HTTP interface. Batches (not
//! one-row-per-insert) because ClickHouse's MergeTree write path is
//! optimized for bulk inserts — many small inserts create excessive parts
//! and force constant background merges.

use serde::Serialize;

use crate::model::{Domain, FeatureEvent, Payload, RawEvent};

pub struct ClickHouseSink {
    client: reqwest::Client,
    base_url: String,
    database: String,
    user: String,
    password: String,
}

/// Flattened row for `risk.raw_events` — see infra/clickhouse/init/01_schema.sql.
/// feature-service is the natural place to write this: it already consumes
/// every raw event to build windows, so this adds a Vec push, not a second
/// Kafka consumer. This table is what tests/eval joins against
/// `scenario_label` ground truth (see docs/metrics.md) and what
/// scripts/train_xgboost.py trains against — without it those have nothing
/// to read.
#[derive(Serialize)]
struct RawEventRow<'a> {
    event_id: &'a uuid::Uuid,
    domain: &'a str,
    entity_key: &'a str,
    source: &'a str,
    seq: u64,
    ts_event: &'a str,
    ts_ingest: &'a str,
    corrupted: bool,
    scenario_label: &'a str,

    m_symbol: Option<&'a str>,
    m_price: Option<f64>,
    m_size: Option<f64>,
    m_side: Option<&'static str>,
    m_bid: Option<f64>,
    m_ask: Option<f64>,
    m_exchange_latency_ms: Option<f64>,

    p_txn_id: Option<uuid::Uuid>,
    p_merchant_id: Option<&'a str>,
    p_account_id_hash: Option<&'a str>,
    p_amount: Option<f64>,
    p_currency: Option<&'a str>,
    p_channel: Option<&'static str>,
    p_country: Option<&'a str>,
    p_processing_latency_ms: Option<f64>,
    p_status: Option<&'static str>,
}

fn side_str(s: crate::model::Side) -> &'static str {
    match s {
        crate::model::Side::Buy => "buy",
        crate::model::Side::Sell => "sell",
    }
}

fn channel_str(c: crate::model::Channel) -> &'static str {
    match c {
        crate::model::Channel::CardPresent => "card_present",
        crate::model::Channel::CardNotPresent => "card_not_present",
        crate::model::Channel::Wire => "wire",
        crate::model::Channel::Ach => "ach",
    }
}

fn status_str(s: crate::model::Status) -> &'static str {
    match s {
        crate::model::Status::Approved => "approved",
        crate::model::Status::Declined => "declined",
        crate::model::Status::Error => "error",
    }
}

fn to_raw_event_row(event: &RawEvent) -> RawEventRow<'_> {
    let mut row = RawEventRow {
        event_id: &event.event_id,
        domain: event.domain.as_str(),
        entity_key: &event.entity_key,
        source: &event.source,
        seq: event.seq,
        ts_event: &event.ts_event,
        ts_ingest: event.ts_ingest.as_deref().unwrap_or(&event.ts_event),
        corrupted: event.corrupted,
        scenario_label: event.scenario_label.as_deref().unwrap_or(""),
        m_symbol: None,
        m_price: None,
        m_size: None,
        m_side: None,
        m_bid: None,
        m_ask: None,
        m_exchange_latency_ms: None,
        p_txn_id: None,
        p_merchant_id: None,
        p_account_id_hash: None,
        p_amount: None,
        p_currency: None,
        p_channel: None,
        p_country: None,
        p_processing_latency_ms: None,
        p_status: None,
    };
    match (&event.payload, event.domain) {
        (Payload::Market(p), Domain::Market) => {
            row.m_symbol = Some(&p.symbol);
            row.m_price = Some(p.price);
            row.m_size = Some(p.size);
            row.m_side = Some(side_str(p.side));
            row.m_bid = Some(p.bid);
            row.m_ask = Some(p.ask);
            row.m_exchange_latency_ms = Some(p.exchange_latency_ms);
        }
        (Payload::Payments(p), Domain::Payments) => {
            row.p_txn_id = Some(p.txn_id);
            row.p_merchant_id = Some(&p.merchant_id);
            row.p_account_id_hash = Some(&p.account_id_hash);
            row.p_amount = Some(p.amount);
            row.p_currency = Some(&p.currency);
            row.p_channel = Some(channel_str(p.channel));
            row.p_country = Some(&p.country);
            row.p_processing_latency_ms = Some(p.processing_latency_ms);
            row.p_status = Some(status_str(p.status));
        }
        // untagged payload deserialization guarantees domain and payload
        // variant agree; this arm exists only so the match is exhaustive.
        _ => {}
    }
    row
}

impl ClickHouseSink {
    pub fn new(base_url: String, database: String, user: String, password: String) -> Self {
        Self { client: reqwest::Client::new(), base_url, database, user, password }
    }

    async fn insert<T: Serialize>(&self, table: &str, rows: &[T]) -> Result<(), String> {
        if rows.is_empty() {
            return Ok(());
        }
        let mut body = String::new();
        for row in rows {
            let line = serde_json::to_string(row).map_err(|e| e.to_string())?;
            body.push_str(&line);
            body.push('\n');
        }

        let mut url = reqwest::Url::parse(&format!("{}/", self.base_url)).map_err(|e| e.to_string())?;
        url.query_pairs_mut()
            .append_pair("database", &self.database)
            .append_pair("query", &format!("INSERT INTO {table} FORMAT JSONEachRow"))
            // Both row types serialize timestamps as RFC3339 (`...T...+00:00`)
            // to match docs/data-contracts.md; ClickHouse's default
            // DateTime64 parser only accepts `YYYY-MM-DD HH:MM:SS.sss`.
            // best_effort parses RFC3339 directly instead.
            .append_pair("date_time_input_format", "best_effort");

        let resp = self
            .client
            .post(url)
            .basic_auth(&self.user, Some(&self.password))
            .body(body)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(format!("clickhouse insert into {table} failed: {status} {text}"));
        }
        Ok(())
    }

    pub async fn insert_features(&self, rows: &[FeatureEvent]) -> Result<(), String> {
        self.insert("features", rows).await
    }

    pub async fn insert_raw_events(&self, rows: &[RawEvent]) -> Result<(), String> {
        let flattened: Vec<RawEventRow<'_>> = rows.iter().map(to_raw_event_row).collect();
        self.insert("raw_events", &flattened).await
    }
}
