"""Isotonic probability calibration for XGBoostDetector, fit offline
(scripts/train_xgboost.py) on a held-out split never used to train the
booster itself — calibrating on training data would systematically
overstate calibration quality, since the model has already memorized some
of its noise. `objective: binary:logistic` already outputs values in
[0, 1], but "in [0, 1]" isn't the same claim as "well-calibrated" (a
predicted 0.8 should mean an observed positive rate near 80% among windows
scored near 0.8) — small, imbalanced training sets like this one routinely
aren't, which is exactly what the before/after Brier score in
scripts/train_xgboost.py's report is there to check, not just assert.

Serialized as a plain JSON piecewise-linear mapping (`x`/`y` breakpoints)
rather than a pickled sklearn object, so it's inspectable and doesn't tie
the runtime to whatever sklearn version trained it — consistent with this
project's other JSON-artifact choices (rules.yaml, docs/benchmarks/latest.json).
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression


def fit_isotonic_calibration(raw_scores: np.ndarray, labels: np.ndarray) -> dict:
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_scores, labels)
    # IsotonicRegression's fitted step function, as explicit breakpoints -
    # np.interp reproduces the same piecewise-linear mapping without needing
    # the sklearn estimator object itself at inference time.
    return {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}


def apply_calibration(raw_score: float, calibration: dict | None) -> float:
    if not calibration or not calibration.get("x"):
        return raw_score
    return float(np.interp(raw_score, calibration["x"], calibration["y"]))


def brier_score(probs: np.ndarray, labels: np.ndarray) -> float:
    """Mean squared error between predicted probability and the (0/1)
    outcome - lower is better-calibrated. Unlike AUCPR/AUC, this is
    sensitive to the actual predicted *values*, not just their ranking,
    which is exactly what calibration (as opposed to discrimination) is
    supposed to improve."""
    probs = np.asarray(probs, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.float64)
    return float(np.mean((probs - labels) ** 2))
