from itertools import pairwise

import numpy as np

from app.calibration import apply_calibration, brier_score, fit_isotonic_calibration


def test_fit_isotonic_calibration_is_monotonic_non_decreasing():
    rng = np.random.default_rng(0)
    raw = rng.uniform(0, 1, size=200)
    labels = (raw + rng.normal(0, 0.1, size=200) > 0.5).astype(int)

    calib = fit_isotonic_calibration(raw, labels)
    calibrated = [apply_calibration(s, calib) for s in sorted(raw)]
    assert all(a <= b + 1e-9 for a, b in pairwise(calibrated))


def test_apply_calibration_with_no_calibration_data_is_identity():
    assert apply_calibration(0.42, None) == 0.42
    assert apply_calibration(0.42, {"x": [], "y": []}) == 0.42


def test_calibration_improves_brier_score_on_systematically_miscalibrated_scores():
    # every raw score is inflated by a fixed offset (e.g. the model is
    # systematically overconfident) - calibration should visibly correct
    # this even though the *ranking* of scores (and thus AUCPR) is untouched.
    rng = np.random.default_rng(1)
    true_prob = rng.uniform(0, 0.5, size=500)
    labels = (rng.uniform(0, 1, size=500) < true_prob).astype(int)
    inflated_raw = np.clip(true_prob + 0.3, 0, 1)

    before = brier_score(inflated_raw, labels)
    calib = fit_isotonic_calibration(inflated_raw, labels)
    calibrated = np.array([apply_calibration(s, calib) for s in inflated_raw])
    after = brier_score(calibrated, labels)

    assert after < before


def test_brier_score_is_zero_for_perfect_predictions():
    assert brier_score(np.array([1.0, 0.0, 1.0]), np.array([1, 0, 1])) == 0.0
