import numpy as np

from app.explain import classify_probable_cause, compute_top_features
from app.schemas import DetectorScores, FeatureEvent, TopFeature


def make_feature_event(domain="market", **overrides) -> FeatureEvent:
    base = {
        "entity_key": "BTC-USD" if domain == "market" else "merch_1",
        "domain": domain,
        "window_start": "2026-01-01T00:00:00+00:00",
        "window_end": "2026-01-01T00:00:02+00:00",
        "window_size_s": 2.0,
        "count": 20,
        "throughput_eps": 10.0,
        "latency_p50_ms": 5.0,
        "latency_p99_ms": 12.0,
        "error_rate": 0.0,
        "vwap": 100.0,
        "spread_bps": 2.0,
        "realized_vol": 0.5,
        "order_imbalance": 0.0,
        "mean_amount": None,
        "sum_amount": None,
        "decline_rate": None,
        "distinct_accounts": None,
        "ewma_mean": 0.5,
        "ewma_var": 0.01,
        "zscore": 0.5,
        "primary_metric": 0.5,
    }
    base.update(overrides)
    return FeatureEvent(**base)


def make_detectors(**overrides) -> DetectorScores:
    base = {"zscore": 0.3, "ewma": 0.3, "cusum": 0.2, "isolation_forest": 0.3, "autoencoder": 0.3, "xgboost": None}
    base.update(overrides)
    return DetectorScores(**base)


def test_compute_top_features_ranks_largest_deviation_first():
    vector = [0.1, 5.0, 0.1, 0.1, 0.1, 0.1, 0.1]  # realized_vol (index 1) is the outlier
    mean = np.zeros(7)
    std = np.ones(7)
    top = compute_top_features("market", vector, mean, std, k=3)
    assert top[0].feature == "realized_vol"


def test_compute_top_features_without_baseline_uses_zero_mean_unit_std():
    vector = [0.0, 0.0, 9.0, 0.0, 0.0, 0.0, 0.0]
    top = compute_top_features("market", vector, None, None, k=1)
    assert top[0].feature == "spread_bps"


def test_shap_contributions_when_provided_override_the_zscore_heuristic():
    # spread_bps (index 2) has the largest *raw deviation* from baseline,
    # but a real SHAP explanation says order_imbalance (index 3) actually
    # drove the model's decision - when shap_contributions is provided,
    # that must win, not the deviation heuristic.
    vector = [0.1, 0.1, 9.0, 0.1, 0.1, 0.1, 0.1]
    mean = np.zeros(7)
    std = np.ones(7)
    shap = [0.01, 0.01, 0.02, 0.85, 0.01, 0.01, 0.01]
    top = compute_top_features("market", vector, mean, std, k=1, shap_contributions=shap)
    assert top[0].feature == "order_imbalance"


def test_shap_contributions_preserve_sign_unlike_the_zscore_heuristic():
    # a negative SHAP value (this feature pushed *toward* normal) must stay
    # negative in the resulting TopFeature - the fallback heuristic is
    # always non-negative (np.abs), but a real SHAP explanation is only
    # useful if direction survives, not just magnitude.
    vector = [0.1] * 7
    mean = np.zeros(7)
    std = np.ones(7)
    shap = [0.0, -0.9, 0.0, 0.0, 0.0, 0.0, 0.0]
    top = compute_top_features("market", vector, mean, std, k=1, shap_contributions=shap)
    assert top[0].feature == "realized_vol"
    assert top[0].contribution == -0.9


def test_market_latency_dominant_classifies_latency_incident():
    f = make_feature_event(domain="market")
    top = [TopFeature(feature="latency_p99_ms", value=500.0, baseline=10.0, contribution=5.0)]
    cause = classify_probable_cause("market", f, top, make_detectors())
    assert cause == "latency_incident"


def test_market_volume_dominant_classifies_volume_spike():
    f = make_feature_event(domain="market")
    top = [TopFeature(feature="throughput_eps", value=500.0, baseline=10.0, contribution=5.0)]
    cause = classify_probable_cause("market", f, top, make_detectors())
    assert cause == "volume_spike"


def test_market_vol_dominant_classifies_volatility_spike():
    f = make_feature_event(domain="market")
    top = [TopFeature(feature="realized_vol", value=5.0, baseline=0.5, contribution=4.5)]
    cause = classify_probable_cause("market", f, top, make_detectors())
    assert cause == "volatility_spike"


def test_market_sustained_cusum_classifies_regime_change():
    f = make_feature_event(domain="market")
    top = [TopFeature(feature="realized_vol", value=1.0, baseline=0.5, contribution=1.0)]
    detectors = make_detectors(cusum=0.9, zscore=0.1)
    cause = classify_probable_cause("market", f, top, detectors)
    assert cause == "regime_change"


def test_payments_concentrated_decline_classifies_fraud_pattern():
    f = make_feature_event(domain="payments", decline_rate=0.3, distinct_accounts=2, mean_amount=500.0)
    top = [TopFeature(feature="log_mean_amount", value=6.0, baseline=4.0, contribution=2.0)]
    cause = classify_probable_cause("payments", f, top, make_detectors())
    assert cause == "fraud_pattern"


def test_payments_error_rate_dominant_classifies_data_corruption():
    f = make_feature_event(domain="payments", decline_rate=0.01, distinct_accounts=50)
    top = [TopFeature(feature="error_rate", value=0.5, baseline=0.0, contribution=0.5)]
    cause = classify_probable_cause("payments", f, top, make_detectors())
    assert cause == "data_corruption"


def test_no_top_features_falls_back_to_unknown():
    f = make_feature_event(domain="market")
    cause = classify_probable_cause("market", f, [], make_detectors())
    assert cause == "unknown"
