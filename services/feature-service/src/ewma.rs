//! Exponentially-weighted moving mean/variance, one instance per entity,
//! persisted across windows (unlike the rest of `WindowAccumulator`, which
//! resets every window). The z-score is computed against the *pre-update*
//! statistics — the new sample is scored against what the model believed
//! *before* seeing it, then folds into the baseline. Scoring against
//! post-update stats would let every anomaly partially absorb itself into
//! its own baseline, which is exactly backwards for detection.

const MIN_VARIANCE: f64 = 1e-9;

pub struct EwmaState {
    alpha: f64,
    mean: f64,
    var: f64,
    initialized: bool,
}

pub struct Observation {
    pub zscore: f64,
    pub ewma_mean: f64,
    pub ewma_var: f64,
}

impl EwmaState {
    pub fn new(alpha: f64) -> Self {
        Self { alpha, mean: 0.0, var: 0.0, initialized: false }
    }

    /// Scores `x` against the current baseline, then updates the baseline
    /// with `x`. Returns the pre-update mean/var alongside the z-score so
    /// callers can persist exactly what the score was computed from.
    pub fn observe(&mut self, x: f64) -> Observation {
        if !self.initialized {
            // First sample for this entity: nothing to compare against yet.
            self.mean = x;
            self.var = 0.0;
            self.initialized = true;
            return Observation { zscore: 0.0, ewma_mean: self.mean, ewma_var: self.var };
        }

        let pre_mean = self.mean;
        let pre_var = self.var.max(MIN_VARIANCE);
        let zscore = (x - pre_mean) / pre_var.sqrt();

        let delta = x - self.mean;
        self.mean += self.alpha * delta;
        self.var = (1.0 - self.alpha) * (self.var + self.alpha * delta * delta);

        Observation { zscore, ewma_mean: pre_mean, ewma_var: pre_var }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_observation_has_zero_zscore_and_seeds_mean() {
        let mut ewma = EwmaState::new(0.1);
        let obs = ewma.observe(42.0);
        assert_eq!(obs.zscore, 0.0);
        assert_eq!(obs.ewma_mean, 42.0);
    }

    #[test]
    fn stable_series_keeps_zscore_near_zero() {
        let mut ewma = EwmaState::new(0.2);
        ewma.observe(10.0);
        let mut last = Observation { zscore: 0.0, ewma_mean: 0.0, ewma_var: 0.0 };
        for _ in 0..50 {
            last = ewma.observe(10.0);
        }
        assert!(last.zscore.abs() < 1e-6, "zscore should collapse to ~0 on a constant series, got {}", last.zscore);
    }

    #[test]
    fn sudden_spike_produces_large_zscore() {
        let mut ewma = EwmaState::new(0.1);
        for _ in 0..30 {
            ewma.observe(10.0 + (rand::random::<f64>() - 0.5) * 0.2);
        }
        let obs = ewma.observe(1000.0);
        assert!(obs.zscore > 10.0, "expected a large zscore for a 100x spike, got {}", obs.zscore);
    }

    #[test]
    fn mean_tracks_a_slow_drift_over_many_observations() {
        let mut ewma = EwmaState::new(0.05);
        ewma.observe(10.0);
        for _ in 0..500 {
            ewma.observe(20.0);
        }
        let obs = ewma.observe(20.0);
        assert!((obs.ewma_mean - 20.0).abs() < 0.5, "ewma mean should have drifted to ~20, got {}", obs.ewma_mean);
    }
}
