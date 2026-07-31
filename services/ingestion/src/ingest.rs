use std::time::Instant;

use futures_util::StreamExt;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::Instrument;

use crate::backoff::backoff_delay;
use crate::metrics::Metrics;
use crate::model::RawEvent;
use crate::sink::EventSink;

/// Connect → stream → on any disconnect, backoff and reconnect. Runs forever;
/// the process is meant to be supervised by the container runtime, not by
/// application-level "give up after N attempts" logic — a data feed outage
/// is not a reason to exit.
pub async fn run<S: EventSink>(ws_url: &str, sink: &S, metrics: &Metrics) -> ! {
    let mut attempt: u32 = 0;
    loop {
        metrics.ws_reconnects_total.inc();
        match connect_async(ws_url).await {
            Ok((stream, _response)) => {
                tracing::info!(url = ws_url, "connected to data source");
                metrics.ws_connected.set(1);
                attempt = 0;
                if let Err(e) = handle_connection(stream, sink, metrics).await {
                    tracing::warn!(error = %e, "data source connection dropped");
                }
                metrics.ws_connected.set(0);
            }
            Err(e) => {
                tracing::warn!(error = %e, attempt, "failed to connect to data source");
            }
        }
        let delay = backoff_delay(attempt);
        attempt = attempt.saturating_add(1);
        tokio::time::sleep(delay).await;
    }
}

async fn handle_connection<S, T>(
    stream: tokio_tungstenite::WebSocketStream<T>,
    sink: &S,
    metrics: &Metrics,
) -> Result<(), tokio_tungstenite::tungstenite::Error>
where
    S: EventSink,
    T: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let (_write, mut read) = stream.split();
    while let Some(frame) = read.next().await {
        let frame = frame?;
        let Message::Text(text) = frame else { continue };
        handle_text_frame(&text, sink, metrics).await;
    }
    Ok(())
}

/// data-generator may batch several events into one WS frame as
/// newline-delimited JSON at higher target rates (see its
/// `_producer_loop`'s `TICK_INTERVAL_S`) rather than one frame per event -
/// most frames still carry exactly one line, which this handles identically
/// to before a one-line split is just that line.
async fn handle_text_frame<S: EventSink>(text: &str, sink: &S, metrics: &Metrics) {
    for line in text.split('\n') {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        handle_frame(line, sink, metrics).await;
    }
}

async fn handle_frame<S: EventSink>(text: &str, sink: &S, metrics: &Metrics) {
    let received_at = Instant::now();
    let mut event: RawEvent = match serde_json::from_str(text) {
        Ok(event) => event,
        Err(e) => {
            metrics.parse_errors_total.inc();
            tracing::warn!(error = %e, "failed to parse raw event, dropping");
            return;
        }
    };
    event.ts_ingest = Some(chrono::Utc::now().to_rfc3339());
    let domain = event.domain.as_str();

    // Root span for this event's whole trace: feature-service and
    // ml-inference each continue it via the traceparent header this span's
    // context gets injected into below (see EventSink::send /
    // crate::telemetry) - one trace per event, spanning every Kafka hop.
    let span = tracing::info_span!("ingest_event", entity_key = %event.entity_key, domain = %domain, event_id = %event.event_id);
    async {
        metrics.inflight_sends.inc();
        let result = sink.send(&event).await;
        metrics.inflight_sends.dec();

        match result {
            Ok(()) => {
                metrics.events_total.with_label_values(&[domain]).inc();
                let elapsed_ms = received_at.elapsed().as_secs_f64() * 1000.0;
                metrics.ws_to_kafka_ms.observe(elapsed_ms);
            }
            Err(e) => {
                metrics.kafka_errors_total.inc();
                tracing::warn!(error = %e, entity = %event.entity_key, "failed to produce event to kafka");
            }
        }
    }
    .instrument(span)
    .await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sink::test_support::MockSink;

    #[tokio::test]
    async fn valid_frame_is_forwarded_to_sink_with_ts_ingest_stamped() {
        let sink = MockSink::default();
        let metrics = Metrics::new();
        let json = r#"{
            "event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
            "domain": "market",
            "entity_key": "BTC-USD",
            "source": "synthetic-exchange-1",
            "seq": 1,
            "ts_event": "2026-07-30T23:09:30.852113+00:00",
            "corrupted": false,
            "payload": {
                "symbol": "BTC-USD", "price": 65000.0, "size": 0.1, "side": "sell",
                "bid": 64990.0, "ask": 65010.0, "exchange_latency_ms": 3.5
            }
        }"#;
        handle_frame(json, &sink, &metrics).await;
        let sent = sink.sent.lock().unwrap();
        assert_eq!(sent.len(), 1);
        assert!(sent[0].ts_ingest.is_some());
        assert_eq!(metrics.events_total.with_label_values(&["market"]).get(), 1);
        assert_eq!(metrics.parse_errors_total.get(), 0);
    }

    #[tokio::test]
    async fn malformed_frame_increments_parse_errors_and_is_dropped() {
        let sink = MockSink::default();
        let metrics = Metrics::new();
        handle_frame("{not valid json", &sink, &metrics).await;
        assert_eq!(sink.sent.lock().unwrap().len(), 0);
        assert_eq!(metrics.parse_errors_total.get(), 1);
    }

    #[tokio::test]
    async fn sink_failure_increments_kafka_errors_not_events_total() {
        let sink = MockSink::default();
        sink.fail_next.store(true, std::sync::atomic::Ordering::SeqCst);
        let metrics = Metrics::new();
        let json = r#"{
            "event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
            "domain": "payments",
            "entity_key": "merch_grocery_01",
            "source": "synthetic-psp-1",
            "seq": 1,
            "ts_event": "2026-07-30T23:09:30.852113+00:00",
            "corrupted": false,
            "payload": {
                "txn_id": "9069c681-fa99-41dd-98a2-c4af7df360d1",
                "merchant_id": "merch_grocery_01", "account_id_hash": "abc",
                "amount": 10.0, "currency": "USD", "channel": "card_present",
                "country": "US", "processing_latency_ms": 5.0, "status": "approved"
            }
        }"#;
        handle_frame(json, &sink, &metrics).await;
        assert_eq!(sink.sent.lock().unwrap().len(), 0);
        assert_eq!(metrics.kafka_errors_total.get(), 1);
        assert_eq!(metrics.events_total.with_label_values(&["payments"]).get(), 0);
    }

    fn market_json(seq: u64) -> String {
        // Compact, single-line - matches what data-generator's
        // `model_dump_json()` actually produces (no embedded newlines),
        // unlike this file's other test fixtures above (which are
        // pretty-printed for readability but only ever passed to
        // `handle_frame` directly, never split on '\n' the way a batched
        // frame is).
        format!(
            r#"{{"event_id": "9069c681-fa99-41dd-98a2-c4af7df360d1", "domain": "market", "entity_key": "BTC-USD", "source": "synthetic-exchange-1", "seq": {seq}, "ts_event": "2026-07-30T23:09:30.852113+00:00", "corrupted": false, "payload": {{"symbol": "BTC-USD", "price": 65000.0, "size": 0.1, "side": "sell", "bid": 64990.0, "ask": 65010.0, "exchange_latency_ms": 3.5}}}}"#
        )
    }

    #[tokio::test]
    async fn newline_batched_frame_forwards_every_event() {
        // data-generator batches several events per WS frame at higher
        // target rates (see its _producer_loop) - a single frame carrying
        // 3 newline-joined events must forward all 3, not just the first.
        let sink = MockSink::default();
        let metrics = Metrics::new();
        let batch = format!("{}\n{}\n{}", market_json(1), market_json(2), market_json(3));
        handle_text_frame(&batch, &sink, &metrics).await;
        let sent = sink.sent.lock().unwrap();
        assert_eq!(sent.len(), 3);
        assert_eq!(sent.iter().map(|e| e.seq).collect::<Vec<_>>(), vec![1, 2, 3]);
    }

    #[tokio::test]
    async fn single_line_frame_behaves_exactly_as_before() {
        let sink = MockSink::default();
        let metrics = Metrics::new();
        handle_text_frame(&market_json(1), &sink, &metrics).await;
        assert_eq!(sink.sent.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn blank_lines_in_a_batch_are_skipped_not_parsed() {
        let sink = MockSink::default();
        let metrics = Metrics::new();
        let batch = format!("{}\n\n{}\n", market_json(1), market_json(2));
        handle_text_frame(&batch, &sink, &metrics).await;
        assert_eq!(sink.sent.lock().unwrap().len(), 2);
        assert_eq!(metrics.parse_errors_total.get(), 0);
    }
}

