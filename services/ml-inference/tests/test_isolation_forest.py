import numpy as np

from app.detectors.isolation_forest import RollingIsolationForest


def _make(min_buffer=30, retrain_every=30):
    return RollingIsolationForest(buffer_size=500, min_buffer=min_buffer, retrain_every=retrain_every, n_estimators=50, contamination=0.05)


def test_not_ready_before_min_buffer():
    det = _make(min_buffer=30)
    for _ in range(29):
        det.observe([0.0] * 5)
    assert not det.ready
    assert det.score([0.0] * 5) == 0.0


def test_ready_after_min_buffer():
    det = _make(min_buffer=30)
    rng = np.random.default_rng(0)
    for _ in range(30):
        det.observe(rng.normal(size=5).tolist())
    assert det.ready


def test_outlier_scores_higher_than_typical_point():
    det = _make(min_buffer=50)
    rng = np.random.default_rng(0)
    for _ in range(200):
        det.observe(rng.normal(loc=0.0, scale=1.0, size=5).tolist())

    normal_score = det.score(rng.normal(loc=0.0, scale=1.0, size=5).tolist())
    outlier_score = det.score([50.0, 50.0, 50.0, 50.0, 50.0])
    assert outlier_score > normal_score


def test_buffer_snapshot_reflects_observations():
    det = _make(min_buffer=100_000)  # never trains
    det.observe([1.0, 2.0])
    det.observe([3.0, 4.0])
    snap = det.buffer_snapshot()
    assert snap == [[1.0, 2.0], [3.0, 4.0]]
