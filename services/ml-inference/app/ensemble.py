"""Combines per-detector scores into one `anomaly_score`. Weighted average,
not max/vote: a max-of-detectors ensemble makes every false-positive-prone
detector a single point of failure for the whole alert stream, and a vote
needs an odd count to avoid ties. Weighted average lets one shaky detector
(e.g. Isolation Forest still warming up) be outvoted by the rest instead of
dominating, while still letting a single very confident detector (z-score
catching an obvious 10-sigma spike) pull the score up on its own.
"""

from __future__ import annotations

from .schemas import DetectorScores

# xgboost is excluded from the base weights and, when present, blended in
# on top — it's the only supervised signal and the only one with a real
# precision/recall number behind it (see docs/metrics.md), so it's trusted
# more, but its absence (no trained model yet) must not silently zero out
# the ensemble.
_BASE_WEIGHTS = {
    "zscore": 0.15,
    "ewma": 0.20,
    "cusum": 0.20,
    "isolation_forest": 0.20,
    "autoencoder": 0.25,
}
_XGBOOST_WEIGHT = 0.35


def combine(detectors: DetectorScores) -> float:
    base_scores = {
        "zscore": detectors.zscore,
        "ewma": detectors.ewma,
        "cusum": detectors.cusum,
        "isolation_forest": detectors.isolation_forest,
        "autoencoder": detectors.autoencoder,
    }

    if detectors.xgboost is None:
        weights = _BASE_WEIGHTS
        total_weight = sum(weights.values())
        score = sum(base_scores[k] * w for k, w in weights.items()) / total_weight
    else:
        remaining = 1.0 - _XGBOOST_WEIGHT
        base_total = sum(_BASE_WEIGHTS.values())
        score = _XGBOOST_WEIGHT * detectors.xgboost
        score += sum(base_scores[k] * (w / base_total) * remaining for k, w in _BASE_WEIGHTS.items())

    return max(0.0, min(1.0, score))
