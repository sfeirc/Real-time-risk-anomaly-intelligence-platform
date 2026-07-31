"""Turns a `FeatureEvent` into the fixed-order numeric vector every
multivariate detector (Isolation Forest, autoencoder, XGBoost, drift PSI/KS)
shares. One definition, imported everywhere, so "feature 3" means the same
thing in every detector and in the explanation's `top_features` output.

`to_vector` stays a pure function of one `FeatureEvent` plus the caller-
supplied `velocity_count` - the multi-window rolling state that value comes
from (see app/detectors/velocity.py) lives in the caller (MLPipeline), not
here, so this module keeps its "one FeatureEvent in, one vector out" shape
and stays trivially unit-testable without needing a whole pipeline.
"""

from __future__ import annotations

import math

from .schemas import FeatureEvent

MARKET_FEATURES = ["zscore", "realized_vol", "spread_bps", "order_imbalance", "throughput_eps", "latency_p99_ms", "error_rate", "velocity_count"]
PAYMENTS_FEATURES = ["zscore", "log_mean_amount", "decline_rate", "distinct_accounts", "throughput_eps", "latency_p99_ms", "error_rate", "velocity_count"]


def feature_names(domain: str) -> list[str]:
    return MARKET_FEATURES if domain == "market" else PAYMENTS_FEATURES


def to_vector(f: FeatureEvent, velocity_count: float) -> list[float]:
    if f.domain == "market":
        return [
            f.zscore,
            f.realized_vol or 0.0,
            f.spread_bps or 0.0,
            f.order_imbalance or 0.0,
            f.throughput_eps,
            f.latency_p99_ms,
            f.error_rate,
            velocity_count,
        ]
    return [
        f.zscore,
        math.log1p(max(f.mean_amount or 0.0, 0.0)),
        f.decline_rate or 0.0,
        float(f.distinct_accounts or 0),
        f.throughput_eps,
        f.latency_p99_ms,
        f.error_rate,
        velocity_count,
    ]
