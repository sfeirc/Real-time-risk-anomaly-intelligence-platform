"""Per-domain Isolation Forest over a rolling buffer of recent feature
vectors. sklearn's IsolationForest has no incremental `partial_fit`, so
"online" here means periodic batch refit on the most recent N windows
(config: `buffer_size`, `retrain_every_n_windows`), not per-sample updates —
the standard pattern for unsupervised detectors without a streaming variant.
The buffer is assumed mostly-normal (anomalies are a small minority of live
traffic); that assumption is exactly what `contamination` encodes.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from sklearn.ensemble import IsolationForest


class RollingIsolationForest:
    def __init__(
        self,
        buffer_size: int,
        min_buffer: int,
        retrain_every: int,
        n_estimators: int,
        contamination: float,
    ) -> None:
        self._buffer: deque[list[float]] = deque(maxlen=buffer_size)
        self._min_buffer = min_buffer
        self._retrain_every = retrain_every
        self._n_estimators = n_estimators
        self._contamination = contamination
        self._since_retrain = 0
        self._model: IsolationForest | None = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def buffer_snapshot(self) -> list[list[float]]:
        """Exposed so DriftMonitor can freeze the first-fit buffer as its
        baseline without reaching into a private field."""
        return list(self._buffer)

    @property
    def mean(self) -> np.ndarray | None:
        return self._mean

    @property
    def std(self) -> np.ndarray | None:
        return self._std

    def observe(self, vector: list[float]) -> None:
        self._buffer.append(vector)
        self._since_retrain += 1
        if len(self._buffer) >= self._min_buffer and (self._model is None or self._since_retrain >= self._retrain_every):
            self._retrain()

    def _retrain(self) -> None:
        data = np.asarray(self._buffer, dtype=np.float64)
        mean = data.mean(axis=0)
        std = data.std(axis=0)
        std[std < 1e-9] = 1.0
        normalized = (data - mean) / std

        model = IsolationForest(
            n_estimators=self._n_estimators,
            contamination=self._contamination,
            random_state=42,
        )
        model.fit(normalized)

        self._model = model
        self._mean = mean
        self._std = std
        self._since_retrain = 0

    def score(self, vector: list[float]) -> float:
        """0..1, higher = more anomalous. `0.0` (not `None`) before the
        first fit — see docs/runbook.md's "still warming up" note."""
        if self._model is None or self._mean is None or self._std is None:
            return 0.0
        x = (np.asarray(vector, dtype=np.float64) - self._mean) / self._std
        # decision_function: higher = more normal, roughly in [-0.5, 0.5].
        # Flip and squash to (0, 1) so it's directly ensemble-comparable.
        raw = -float(self._model.decision_function(x.reshape(1, -1))[0])
        return float(1.0 / (1.0 + np.exp(-6.0 * raw)))
