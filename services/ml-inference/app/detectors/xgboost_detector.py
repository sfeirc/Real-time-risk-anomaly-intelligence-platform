"""Supervised detector, trained *offline* (scripts/train_xgboost.py) against
historical `scenario_label` ground truth pulled from ClickHouse, then loaded
here for online scoring. This is the one detector allowed anywhere near
ground-truth labels, and only at training time, run by a human/CI job — the
live service never sees `scenario_label` (see docs/metrics.md). Optional:
`score()` returns `None` until a model artifact exists, matching
`detectors.xgboost: float | null` in docs/data-contracts.md.
"""

from __future__ import annotations

import os

import numpy as np
import xgboost as xgb

from ..features import feature_names


class XGBoostDetector:
    def __init__(self, model_path: str, domain: str) -> None:
        self._model_path = model_path
        # scripts/train_xgboost.py builds its DMatrix with feature_names=...;
        # a booster trained that way validates every predict() call against
        # the same names and raises ValueError on a plain unnamed array —
        # keep this in lockstep with the training script or predict() throws.
        self._feature_names = feature_names(domain)
        self._booster: xgb.Booster | None = None
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._model_path):
            booster = xgb.Booster()
            booster.load_model(self._model_path)
            self._booster = booster

    def reload(self) -> bool:
        """Re-checks disk for a newly trained artifact without a restart."""
        self._load()
        return self._booster is not None

    @property
    def ready(self) -> bool:
        return self._booster is not None

    def score(self, vector: list[float]) -> float | None:
        if self._booster is None:
            return None
        x = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        dmat = xgb.DMatrix(x, feature_names=self._feature_names)
        return float(self._booster.predict(dmat)[0])
