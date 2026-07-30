use crate::model::RawEvent;
use rdkafka::config::ClientConfig;
use rdkafka::error::KafkaError;
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::util::Timeout;
use std::time::Duration;

/// Generic so unit tests can substitute a mock without touching a broker.
/// Native async-fn-in-trait (stable since 1.75): only usable in generic
/// contexts, not as `dyn EventSink` — fine here, we never need the latter.
pub trait EventSink: Send + Sync {
    async fn send(&self, event: &RawEvent) -> Result<(), String>;
}

pub struct KafkaSink {
    producer: FutureProducer,
    topic: String,
}

impl KafkaSink {
    pub fn new(brokers: &str, topic: &str) -> Result<Self, KafkaError> {
        let producer: FutureProducer = ClientConfig::new()
            .set("bootstrap.servers", brokers)
            .set("message.timeout.ms", "10000")
            .set("compression.type", "lz4")
            .set("linger.ms", "5")
            .set("acks", "all")
            .create()?;
        Ok(Self {
            producer,
            topic: topic.to_string(),
        })
    }
}

impl EventSink for KafkaSink {
    async fn send(&self, event: &RawEvent) -> Result<(), String> {
        let payload = serde_json::to_vec(event).map_err(|e| e.to_string())?;
        let record = FutureRecord::to(&self.topic)
            .payload(&payload)
            .key(&event.entity_key);
        self.producer
            .send(record, Timeout::After(Duration::from_secs(5)))
            .await
            .map(|_| ())
            .map_err(|(err, _owned_msg)| err.to_string())
    }
}

#[cfg(test)]
pub mod test_support {
    use super::*;
    use std::sync::Mutex;

    #[derive(Default)]
    pub struct MockSink {
        pub sent: Mutex<Vec<RawEvent>>,
        pub fail_next: std::sync::atomic::AtomicBool,
    }

    impl EventSink for MockSink {
        async fn send(&self, event: &RawEvent) -> Result<(), String> {
            if self
                .fail_next
                .swap(false, std::sync::atomic::Ordering::SeqCst)
            {
                return Err("simulated failure".to_string());
            }
            self.sent.lock().unwrap().push(event.clone());
            Ok(())
        }
    }
}
