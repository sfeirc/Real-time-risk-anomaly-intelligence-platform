use rand::RngExt;
use std::time::Duration;

const BASE_MS: u64 = 200;
const MAX_MS: u64 = 15_000;

/// Full-jitter exponential backoff (AWS Architecture Blog's "Exponential
/// Backoff and Jitter"): capping *and* randomizing the delay avoids a
/// thundering herd of reconnects all retrying in lockstep against a
/// recovering broker/data-generator.
pub fn backoff_delay(attempt: u32) -> Duration {
    let exp = BASE_MS.saturating_mul(1u64 << attempt.min(10));
    let capped = exp.min(MAX_MS);
    let jittered = rand::rng().random_range(0..=capped);
    Duration::from_millis(jittered.max(BASE_MS / 2))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_attempt_is_small() {
        for _ in 0..50 {
            let d = backoff_delay(0);
            assert!(d.as_millis() <= BASE_MS as u128);
        }
    }

    #[test]
    fn never_exceeds_cap_even_for_large_attempts() {
        for attempt in [5, 10, 20, 100] {
            for _ in 0..50 {
                let d = backoff_delay(attempt);
                assert!(d.as_millis() <= MAX_MS as u128, "attempt {attempt} exceeded cap");
            }
        }
    }

    #[test]
    fn always_at_least_the_floor() {
        for attempt in 0..15 {
            let d = backoff_delay(attempt);
            assert!(d.as_millis() >= (BASE_MS / 2) as u128);
        }
    }
}
