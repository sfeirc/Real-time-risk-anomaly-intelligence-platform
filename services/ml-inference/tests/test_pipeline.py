import random

from app.config import Settings
from app.pipeline import MLPipeline
from app.schemas import FeatureEvent


def make_feature_event(entity_key="BTC-USD", domain="market", zscore=0.1, realized_vol=0.5, **overrides) -> FeatureEvent:
    base = {
        "entity_key": entity_key,
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
        "realized_vol": realized_vol,
        "order_imbalance": 0.0,
        "mean_amount": None,
        "sum_amount": None,
        "decline_rate": None,
        "distinct_accounts": None,
        "ewma_mean": 0.5,
        "ewma_var": 0.01,
        "zscore": zscore,
        "primary_metric": realized_vol,
    }
    base.update(overrides)
    return FeatureEvent(**base)


def fast_settings() -> Settings:
    return Settings(
        buffer_size=200,
        min_buffer_for_training=20,
        retrain_every_n_windows=20,
        autoencoder_epochs=15,
        autoencoder_hidden_dim=4,
        autoencoder_latent_dim=2,
        isolation_forest_n_estimators=30,
        xgboost_model_path="tests/does_not_exist_{domain}.json",
    )


def test_stable_stream_produces_no_alerts():
    pipeline = MLPipeline(fast_settings())
    rng = random.Random(1)
    alerts = []
    for _ in range(80):
        z = rng.gauss(0, 0.5)
        f = make_feature_event(zscore=z, realized_vol=0.5 + rng.gauss(0, 0.02))
        alert = pipeline.process(f)
        if alert is not None:
            alerts.append(alert)
    # a handful of false positives from random noise crossing 0.55 is
    # plausible; a stable series should not alert on more than a small
    # fraction of windows.
    assert len(alerts) <= 4


def test_sustained_spike_eventually_produces_an_alert():
    pipeline = MLPipeline(fast_settings())
    rng = random.Random(2)
    for _ in range(60):
        f = make_feature_event(zscore=rng.gauss(0, 0.3), realized_vol=0.5 + rng.gauss(0, 0.02))
        pipeline.process(f)

    alert = None
    for _ in range(20):
        f = make_feature_event(zscore=8.0, realized_vol=5.0)
        result = pipeline.process(f)
        if result is not None:
            alert = result
            break

    assert alert is not None
    assert alert.entity_key == "BTC-USD"
    assert alert.domain == "market"
    assert alert.anomaly_score >= 0.55
    assert alert.severity in ("watch", "alert", "critical")
    assert alert.detectors.zscore > 0.5
    assert alert.explanation.probable_cause != "unknown" or len(alert.explanation.top_features) > 0
    assert alert.latency_ingest_to_alert_ms >= 0.0


def test_xgboost_absent_when_no_model_artifact():
    pipeline = MLPipeline(fast_settings())
    assert not pipeline.xgboost_detectors["market"].ready
    assert not pipeline.xgboost_detectors["payments"].ready


def test_drift_metrics_available_after_warmup():
    pipeline = MLPipeline(fast_settings())
    rng = random.Random(3)
    for _ in range(60):
        f = make_feature_event(zscore=rng.gauss(0, 0.3), realized_vol=0.5 + rng.gauss(0, 0.02))
        pipeline.process(f)

    event = pipeline.check_drift_and_build_metrics("market")
    assert event.model_id == "ensemble-market"
    assert event.events_scored >= 60
    assert set(event.psi_by_feature.keys()) == {"zscore", "realized_vol", "spread_bps", "order_imbalance", "throughput_eps", "latency_p99_ms", "error_rate"}
