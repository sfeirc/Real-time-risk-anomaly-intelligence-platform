import numpy as np

from app.drift import DriftMonitor
from app.features import feature_names


def _normal_vectors(n, dim, seed, loc=0.0, scale=1.0):
    rng = np.random.default_rng(seed)
    return rng.normal(loc=loc, scale=scale, size=(n, dim)).tolist()


def test_no_baseline_returns_zeros_and_no_drift():
    monitor = DriftMonitor()
    psi, _ks, drift = monitor.check("market", _normal_vectors(50, 7, seed=1))
    names = feature_names("market")
    assert set(psi.keys()) == set(names)
    assert all(v == 0.0 for v in psi.values())
    assert drift is False


def test_set_baseline_is_idempotent():
    monitor = DriftMonitor()
    first = _normal_vectors(100, 7, seed=1)
    monitor.set_baseline("market", first)
    monitor.set_baseline("market", _normal_vectors(100, 7, seed=2, loc=50.0))
    assert monitor.has_baseline("market")
    # if the second call had overwritten the baseline, comparing `first`
    # against itself would show drift; it must not.
    _psi, _ks, drift = monitor.check("market", first)
    assert drift is False


def test_identical_distribution_shows_no_drift():
    monitor = DriftMonitor()
    baseline = _normal_vectors(200, 7, seed=1)
    monitor.set_baseline("market", baseline)
    live = _normal_vectors(200, 7, seed=99)  # same distribution, different draw
    _, _, drift = monitor.check("market", live)
    assert drift is False


def test_shifted_distribution_triggers_drift():
    monitor = DriftMonitor()
    baseline = _normal_vectors(200, 7, seed=1, loc=0.0, scale=1.0)
    monitor.set_baseline("market", baseline)
    live = _normal_vectors(200, 7, seed=1, loc=10.0, scale=1.0)  # large shift, every feature
    psi, _ks, drift = monitor.check("market", live)
    assert drift is True
    assert all(v > 0.2 for v in psi.values())


def test_too_few_live_samples_skips_check():
    monitor = DriftMonitor()
    monitor.set_baseline("market", _normal_vectors(200, 7, seed=1))
    _psi, _ks, drift = monitor.check("market", _normal_vectors(3, 7, seed=2))
    assert drift is False
