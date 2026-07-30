//! Mirrors `docs/data-contracts.md` / `schemas/raw_event.schema.json`.
//! Kept as a hand-written struct rather than a shared crate with
//! `data-generator` (Python) and `feature-service` (Rust) deliberately — see
//! ARCHITECTURE.md's rationale for JSON-Schema-as-contract over a shared
//! codegen'd type. `tests/integration` cross-checks all three against the
//! same schema file.

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

/// Untagged: the wire format has no discriminant key on the payload itself
/// (`domain` on the envelope tells you which one it is) — the two payload
/// shapes have disjoint required fields so serde can disambiguate reliably.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deserializes_market_event_from_generator_shape() {
        let json = r#"{
            "event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
            "domain": "market",
            "entity_key": "DOGE-USD",
            "source": "synthetic-exchange-1",
            "seq": 31,
            "ts_event": "2026-07-30T23:09:30.852113+00:00",
            "ts_ingest": null,
            "corrupted": false,
            "scenario_label": null,
            "payload": {
                "symbol": "DOGE-USD",
                "price": 0.14,
                "size": 0.142978,
                "side": "buy",
                "bid": 0.14,
                "ask": 0.14,
                "exchange_latency_ms": 2.168
            }
        }"#;
        let event: RawEvent = serde_json::from_str(json).expect("valid market event");
        assert_eq!(event.domain, Domain::Market);
        assert!(matches!(event.payload, Payload::Market(_)));
    }

    #[test]
    fn deserializes_payments_event_from_generator_shape() {
        let json = r#"{
            "event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
            "domain": "payments",
            "entity_key": "merch_grocery_01",
            "source": "synthetic-psp-1",
            "seq": 5,
            "ts_event": "2026-07-30T23:09:30.852113+00:00",
            "ts_ingest": null,
            "corrupted": true,
            "scenario_label": "data_corruption",
            "payload": {
                "txn_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
                "merchant_id": "merch_grocery_01",
                "account_id_hash": "abc123",
                "amount": -45.0,
                "currency": "USD",
                "channel": "card_present",
                "country": "US",
                "processing_latency_ms": 12.0,
                "status": "approved"
            }
        }"#;
        let event: RawEvent = serde_json::from_str(json).expect("valid payments event");
        assert_eq!(event.domain, Domain::Payments);
        assert!(event.corrupted);
        assert_eq!(event.scenario_label.as_deref(), Some("data_corruption"));
        match event.payload {
            Payload::Payments(p) => assert_eq!(p.amount, -45.0),
            _ => panic!("expected payments payload"),
        }
    }

    #[test]
    fn round_trips_through_serialize_deserialize() {
        let json = r#"{
            "event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
            "domain": "market",
            "entity_key": "BTC-USD",
            "source": "synthetic-exchange-1",
            "seq": 1,
            "ts_event": "2026-07-30T23:09:30.852113+00:00",
            "corrupted": false,
            "payload": {
                "symbol": "BTC-USD",
                "price": 65000.0,
                "size": 0.1,
                "side": "sell",
                "bid": 64990.0,
                "ask": 65010.0,
                "exchange_latency_ms": 3.5
            }
        }"#;
        let mut event: RawEvent = serde_json::from_str(json).unwrap();
        event.ts_ingest = Some("2026-07-30T23:09:30.900000+00:00".to_string());
        let out = serde_json::to_string(&event).unwrap();
        let reparsed: RawEvent = serde_json::from_str(&out).unwrap();
        assert_eq!(reparsed.ts_ingest.as_deref(), Some("2026-07-30T23:09:30.900000+00:00"));
    }
}
