//! Per-entity tumbling window accumulator.
//!
//! Windowing is processing-time (bucketed by wall-clock arrival at this
//! service), not event-time with watermarks. At this pipeline's scale
//! (single-digit-ms ingestion lag, no out-of-order replay) that's a
//! deliberate simplification — event-time windowing buys correctness under
//! reordering/late data at the cost of watermark logic this project doesn't
//! need yet. Noted as the first upgrade in `docs/roadmap.md` if this ever
//! ingested a real, occasionally-late-arriving feed.

use std::collections::HashSet;

use chrono::{DateTime, Utc};

use crate::ewma::EwmaState;
use crate::model::{Domain, FeatureEvent, Payload, RawEvent, Status};

const SECONDS_PER_YEAR: f64 = 365.0 * 24.0 * 3600.0;

struct MarketAccum {
    sum_price_volume: f64,
    sum_volume: f64,
    log_returns: Vec<f64>,
    sum_spread_bps: f64,
    buy_volume: f64,
    sell_volume: f64,
}

impl MarketAccum {
    fn new() -> Self {
        Self { sum_price_volume: 0.0, sum_volume: 0.0, log_returns: Vec::new(), sum_spread_bps: 0.0, buy_volume: 0.0, sell_volume: 0.0 }
    }
}

struct PaymentsAccum {
    sum_amount: f64,
    decline_count: u64,
    distinct_accounts: HashSet<String>,
}

impl PaymentsAccum {
    fn new() -> Self {
        Self { sum_amount: 0.0, decline_count: 0, distinct_accounts: HashSet::new() }
    }
}

pub struct EntityWindow {
    domain: Domain,
    window_size_s: f64,
    window_start: DateTime<Utc>,
    count: u64,
    latencies_ms: Vec<f64>,
    error_count: u64,
    market: Option<MarketAccum>,
    payments: Option<PaymentsAccum>,
    last_price: Option<f64>,
    ewma: EwmaState,
}

impl EntityWindow {
    pub fn new(domain: Domain, window_size_s: f64, ewma_alpha: f64, now: DateTime<Utc>) -> Self {
        Self {
            domain,
            window_size_s,
            window_start: now,
            count: 0,
            latencies_ms: Vec::new(),
            error_count: 0,
            market: matches!(domain, Domain::Market).then(MarketAccum::new),
            payments: matches!(domain, Domain::Payments).then(PaymentsAccum::new),
            last_price: None,
            ewma: EwmaState::new(ewma_alpha),
        }
    }

    pub fn add(&mut self, event: &RawEvent) {
        self.count += 1;
        match &event.payload {
            Payload::Market(p) => {
                self.latencies_ms.push(p.exchange_latency_ms);
                if event.corrupted {
                    self.error_count += 1;
                }
                if let Some(m) = self.market.as_mut() {
                    m.sum_price_volume += p.price * p.size;
                    m.sum_volume += p.size;
                    m.sum_spread_bps += if p.price.abs() > f64::EPSILON { (p.ask - p.bid) / p.price * 1e4 } else { 0.0 };
                    match p.side {
                        crate::model::Side::Buy => m.buy_volume += p.size,
                        crate::model::Side::Sell => m.sell_volume += p.size,
                    }
                    if let Some(prev) = self.last_price
                        && prev > 0.0 && p.price > 0.0 {
                            m.log_returns.push((p.price / prev).ln());
                        }
                    self.last_price = Some(p.price);
                }
            }
            Payload::Payments(p) => {
                self.latencies_ms.push(p.processing_latency_ms);
                let is_error = matches!(p.status, Status::Declined | Status::Error) || event.corrupted;
                if is_error {
                    self.error_count += 1;
                }
                if let Some(pay) = self.payments.as_mut() {
                    pay.sum_amount += p.amount;
                    if matches!(p.status, Status::Declined) {
                        pay.decline_count += 1;
                    }
                    pay.distinct_accounts.insert(p.account_id_hash.clone());
                }
            }
        }
    }

    pub fn should_flush(&self, now: DateTime<Utc>) -> bool {
        (now - self.window_start).num_milliseconds() as f64 >= self.window_size_s * 1000.0
    }

    /// Closes the current window and opens the next one immediately
    /// (fixed-cadence boundaries, not drifting with call jitter). Returns
    /// `None` for windows with zero events — publishing an empty window
    /// would just inject a fake zero-activity sample into that entity's
    /// EWMA baseline.
    pub fn flush(&mut self) -> Option<FeatureEvent> {
        let window_start = self.window_start;
        let window_end = window_start + chrono::Duration::milliseconds((self.window_size_s * 1000.0) as i64);
        self.window_start = window_end;

        if self.count == 0 {
            return None;
        }

        let count = self.count;
        let throughput_eps = count as f64 / self.window_size_s;
        let error_rate = self.error_count as f64 / count as f64;

        self.latencies_ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let latency_p50_ms = percentile(&self.latencies_ms, 0.50);
        let latency_p99_ms = percentile(&self.latencies_ms, 0.99);

        let (vwap, spread_bps, realized_vol, order_imbalance, mean_amount, sum_amount, decline_rate, distinct_accounts, primary_metric);

        match self.domain {
            Domain::Market => {
                let m = self.market.as_ref().expect("market accumulator present for market domain");
                vwap = Some(if m.sum_volume > 0.0 { m.sum_price_volume / m.sum_volume } else { self.last_price.unwrap_or(0.0) });
                spread_bps = Some(m.sum_spread_bps / count as f64);
                let sum_sq_returns: f64 = m.log_returns.iter().map(|r| r * r).sum();
                realized_vol = Some((sum_sq_returns * (SECONDS_PER_YEAR / self.window_size_s)).sqrt());
                let denom = m.buy_volume + m.sell_volume;
                order_imbalance = Some(if denom > 0.0 { (m.buy_volume - m.sell_volume) / denom } else { 0.0 });
                mean_amount = None;
                sum_amount = None;
                decline_rate = None;
                distinct_accounts = None;
                primary_metric = realized_vol.unwrap();
            }
            Domain::Payments => {
                let p = self.payments.as_ref().expect("payments accumulator present for payments domain");
                vwap = None;
                spread_bps = None;
                realized_vol = None;
                order_imbalance = None;
                let mean = p.sum_amount / count as f64;
                mean_amount = Some(mean);
                sum_amount = Some(p.sum_amount);
                decline_rate = Some(p.decline_count as f64 / count as f64);
                distinct_accounts = Some(p.distinct_accounts.len() as u64);
                primary_metric = mean;
            }
        }

        let obs = self.ewma.observe(primary_metric);

        // reset per-window accumulators; last_price and the EWMA state persist.
        self.count = 0;
        self.latencies_ms.clear();
        self.error_count = 0;
        if let Some(m) = self.market.as_mut() {
            *m = MarketAccum::new();
        }
        if let Some(p) = self.payments.as_mut() {
            *p = PaymentsAccum::new();
        }

        Some(FeatureEvent {
            entity_key: String::new(), // filled in by the caller, which owns the key
            domain: self.domain,
            window_start: window_start.to_rfc3339(),
            window_end: window_end.to_rfc3339(),
            window_size_s: self.window_size_s,
            count,
            throughput_eps,
            latency_p50_ms,
            latency_p99_ms,
            error_rate,
            vwap,
            spread_bps,
            realized_vol,
            order_imbalance,
            mean_amount,
            sum_amount,
            decline_rate,
            distinct_accounts,
            ewma_mean: obs.ewma_mean,
            ewma_var: obs.ewma_var,
            zscore: obs.zscore,
            primary_metric,
        })
    }
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{MarketPayload, PaymentsPayload, Side};
    use uuid::Uuid;

    fn market_event(symbol: &str, price: f64, side: Side, latency: f64) -> RawEvent {
        RawEvent {
            event_id: Uuid::new_v4(),
            domain: Domain::Market,
            entity_key: symbol.to_string(),
            source: "test".to_string(),
            seq: 1,
            ts_event: Utc::now().to_rfc3339(),
            ts_ingest: None,
            corrupted: false,
            scenario_label: None,
            payload: Payload::Market(MarketPayload {
                symbol: symbol.to_string(),
                price,
                size: 1.0,
                side,
                bid: price - 1.0,
                ask: price + 1.0,
                exchange_latency_ms: latency,
            }),
        }
    }

    fn payments_event(merchant: &str, amount: f64, status: Status) -> RawEvent {
        RawEvent {
            event_id: Uuid::new_v4(),
            domain: Domain::Payments,
            entity_key: merchant.to_string(),
            source: "test".to_string(),
            seq: 1,
            ts_event: Utc::now().to_rfc3339(),
            ts_ingest: None,
            corrupted: false,
            scenario_label: None,
            payload: Payload::Payments(PaymentsPayload {
                txn_id: Uuid::new_v4(),
                merchant_id: merchant.to_string(),
                account_id_hash: "acct1".to_string(),
                amount,
                currency: "USD".to_string(),
                channel: crate::model::Channel::CardPresent,
                country: "US".to_string(),
                processing_latency_ms: 20.0,
                status,
            }),
        }
    }

    #[test]
    fn empty_window_flush_returns_none() {
        let mut w = EntityWindow::new(Domain::Market, 2.0, 0.1, Utc::now());
        assert!(w.flush().is_none());
    }

    #[test]
    fn market_window_computes_vwap_and_order_imbalance() {
        let mut w = EntityWindow::new(Domain::Market, 2.0, 0.1, Utc::now());
        w.add(&market_event("BTC-USD", 100.0, Side::Buy, 5.0));
        w.add(&market_event("BTC-USD", 102.0, Side::Buy, 5.0));
        w.add(&market_event("BTC-USD", 98.0, Side::Sell, 5.0));
        let f = w.flush().expect("non-empty window");
        assert_eq!(f.count, 3);
        assert!((f.vwap.unwrap() - 100.0).abs() < 1e-6);
        assert!(f.order_imbalance.unwrap() > 0.0, "2 buys vs 1 sell should be positive imbalance");
    }

    #[test]
    fn payments_window_computes_mean_amount_and_decline_rate() {
        let mut w = EntityWindow::new(Domain::Payments, 5.0, 0.1, Utc::now());
        w.add(&payments_event("m1", 100.0, Status::Approved));
        w.add(&payments_event("m1", 200.0, Status::Declined));
        let f = w.flush().expect("non-empty window");
        assert_eq!(f.count, 2);
        assert!((f.mean_amount.unwrap() - 150.0).abs() < 1e-6);
        assert!((f.decline_rate.unwrap() - 0.5).abs() < 1e-6);
    }

    #[test]
    fn last_price_persists_across_window_boundary_for_return_calc() {
        let mut w = EntityWindow::new(Domain::Market, 2.0, 0.1, Utc::now());
        w.add(&market_event("BTC-USD", 100.0, Side::Buy, 5.0));
        w.flush();
        // first tick of the *next* window should produce one log-return
        // against the carried-over last_price, not start from a blank slate.
        w.add(&market_event("BTC-USD", 101.0, Side::Buy, 5.0));
        w.add(&market_event("BTC-USD", 102.0, Side::Buy, 5.0));
        let f = w.flush().expect("non-empty window");
        assert!(f.realized_vol.unwrap() > 0.0);
    }

    #[test]
    fn zscore_spikes_on_anomalous_window_after_stable_baseline() {
        let mut w = EntityWindow::new(Domain::Payments, 1.0, 0.2, Utc::now());
        for _ in 0..20 {
            w.add(&payments_event("m1", 50.0, Status::Approved));
            w.flush();
        }
        for _ in 0..10 {
            w.add(&payments_event("m1", 5000.0, Status::Approved));
        }
        let f = w.flush().expect("non-empty window");
        assert!(f.zscore > 5.0, "expected large zscore for a 100x amount spike, got {}", f.zscore);
    }
}
