"""Turns a scored window into a human-readable explanation: which features
moved the most (`top_features`), and a rule-based `probable_cause` label.
The rules behind `probable_cause` are intentionally simple and legible —
meant to be readable in a dashboard tooltip during a demo, not a model
dump.

`top_features`' `contribution` comes from one of two sources, in order of
preference: XGBoost's own SHAP values (`XGBoostDetector.shap_contributions`)
when that detector is loaded - the one detector trained on real ground
truth, and the one with the highest ensemble weight (see
app/ensemble.py), so its attribution is the most trustworthy available
when present - falling back to a z-like heuristic (deviation from the
Isolation Forest's own rolling mean/std, the same baseline the model
itself was fit on) when it isn't. Both share the same `TopFeature` shape,
so the dashboard and every other consumer don't need to know which one
produced a given alert's explanation.
"""

from __future__ import annotations

import numpy as np

from .features import feature_names
from .schemas import DetectorScores, FeatureEvent, ProbableCause, TopFeature


def compute_top_features(
    domain: str,
    vector: list[float],
    mean: np.ndarray | None,
    std: np.ndarray | None,
    k: int = 3,
    shap_contributions: list[float] | None = None,
) -> list[TopFeature]:
    names = feature_names(domain)
    arr = np.asarray(vector, dtype=np.float64)
    baseline = mean if mean is not None else np.zeros_like(arr)

    if shap_contributions is not None:
        contributions = np.asarray(shap_contributions, dtype=np.float64)
        order = np.argsort(-np.abs(contributions))[:k]
        return [
            TopFeature(feature=names[i], value=float(arr[i]), baseline=float(baseline[i]), contribution=float(contributions[i]))
            for i in order
        ]

    scale = std.copy() if std is not None else np.ones_like(arr)
    scale[scale < 1e-9] = 1.0
    contributions = np.abs((arr - baseline) / scale)
    order = np.argsort(-contributions)[:k]
    return [
        TopFeature(feature=names[i], value=float(arr[i]), baseline=float(baseline[i]), contribution=float(contributions[i]))
        for i in order
    ]


def classify_probable_cause(domain: str, f: FeatureEvent, top_features: list[TopFeature], detectors: DetectorScores) -> ProbableCause:
    top_name = top_features[0].feature if top_features else None
    sustained = detectors.cusum > 0.5 and detectors.cusum > detectors.zscore

    if domain == "payments":
        concentrated_fraud = (f.decline_rate or 0.0) > 0.15 and (f.distinct_accounts or 999) <= 5 and (f.mean_amount or 0.0) > 0.0
        if concentrated_fraud:
            return "fraud_pattern"
        if top_name == "latency_p99_ms":
            return "latency_incident"
        if top_name in ("throughput_eps", "velocity_count"):
            return "volume_spike"
        if top_name == "error_rate":
            return "data_corruption"
        if sustained:
            return "regime_change"
        if top_name in ("log_mean_amount", "decline_rate", "zscore"):
            return "fraud_pattern"
        return "unknown"

    if top_name == "latency_p99_ms":
        return "latency_incident"
    if top_name in ("throughput_eps", "velocity_count"):
        return "volume_spike"
    if top_name == "error_rate":
        return "data_corruption"
    if sustained:
        return "regime_change"
    if top_name in ("realized_vol", "zscore", "spread_bps", "order_imbalance"):
        return "volatility_spike"
    return "unknown"
