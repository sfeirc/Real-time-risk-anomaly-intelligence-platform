"""Zero-training-data detectors, all operating on the z-score
`feature-service` already computes from its per-entity EWMA baseline
(`docs/data-contracts.md` `features.zscore`). These run from window one —
useful on their own, and as a sanity baseline the ML models below have to
beat during eval (see docs/metrics.md).
"""

from __future__ import annotations

import math
from collections import defaultdict, deque


def squash(z: float, scale: float = 4.0) -> float:
    """Maps an unbounded z-score to (0, 1), saturating rather than clipping:
    0 at z=0, ~0.63 at z=scale, approaching 1 for large |z|."""
    return 1.0 - math.exp(-abs(z) / scale)


class ZScoreDetector:
    """Direct read of the point-anomaly signal feature-service already
    computed. Catches sudden single-window spikes; blind to sustained
    smaller shifts — that's what EwmaRunRules and Cusum are for."""

    def score(self, zscore: float) -> float:
        return squash(zscore)


class EwmaRunRulesDetector:
    """Simplified Western Electric run rules: a control-chart technique for
    catching a *sustained* shift too small to trip a single-point z-score
    threshold — e.g. 2 of the last 3 windows both 2+ sigma out, same
    direction. Real drift usually looks like this before it looks like a
    single dramatic spike.
    """

    def __init__(self, history_len: int = 5) -> None:
        self._history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=history_len))

    def score(self, entity_key: str, zscore: float) -> float:
        hist = self._history[entity_key]
        hist.append(zscore)
        points = list(hist)

        if abs(zscore) > 3.0:
            return 1.0

        def same_sign_beyond(pts: list[float], threshold: float) -> int:
            positive = sum(1 for p in pts if p > threshold)
            negative = sum(1 for p in pts if p < -threshold)
            return max(positive, negative)

        last3 = points[-3:]
        if len(last3) == 3 and same_sign_beyond(last3, 2.0) >= 2:
            return 0.8

        last5 = points[-5:]
        if len(last5) == 5 and same_sign_beyond(last5, 1.0) >= 4:
            return 0.6

        return squash(zscore, scale=6.0) * 0.5


class CusumDetector:
    """Tabular CUSUM: accumulates *signed* deviation from baseline instead
    of scoring each window independently, so a series of small same-direction
    shifts adds up to a detection even though no individual window is an
    outlier — exactly the `regime_change` scenario's shape (see
    services/data-generator/app/scenarios.py).
    """

    def __init__(self, k: float = 0.5, h: float = 5.0) -> None:
        self._k = k
        self._h = h
        self._state: dict[str, tuple[float, float]] = {}  # entity -> (s_pos, s_neg)

    def score(self, entity_key: str, zscore: float) -> float:
        s_pos, s_neg = self._state.get(entity_key, (0.0, 0.0))
        s_pos = max(0.0, s_pos + zscore - self._k)
        s_neg = min(0.0, s_neg + zscore + self._k)
        self._state[entity_key] = (s_pos, s_neg)
        magnitude = max(s_pos, -s_neg)
        return min(magnitude / self._h, 1.0)

    def reset(self, entity_key: str) -> None:
        self._state.pop(entity_key, None)
