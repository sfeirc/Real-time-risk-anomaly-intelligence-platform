"""Data/model drift monitor: Population Stability Index and a two-sample
KS test, per feature, comparing the live rolling buffer against the
snapshot the unsupervised models were *first* trained on. PSI catches
distribution shape/bucket shifts cheaply; KS is more sensitive to shifts PSI's
fixed binning can miss (see docs/metrics.md). Both against the same
`baseline` frozen at first fit — not the constantly-refreshed buffer — because
comparing a rolling window to itself can never show drift.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from .features import feature_names

PSI_DRIFT_THRESHOLD = 0.2
KS_PVALUE_THRESHOLD = 0.01
_BINS = 10


def _psi(baseline: np.ndarray, live: np.ndarray) -> float:
    quantiles = np.linspace(0, 100, _BINS + 1)
    edges = np.unique(np.percentile(baseline, quantiles))
    if len(edges) < 3:
        return 0.0  # degenerate (near-constant) feature; nothing meaningful to bucket

    base_counts, _ = np.histogram(baseline, bins=edges)
    live_counts, _ = np.histogram(live, bins=edges)

    base_pct = base_counts / max(base_counts.sum(), 1) + 1e-6
    live_pct = live_counts / max(live_counts.sum(), 1) + 1e-6

    return float(np.sum((live_pct - base_pct) * np.log(live_pct / base_pct)))


class DriftMonitor:
    def __init__(self) -> None:
        self._baseline: dict[str, np.ndarray] = {}

    def has_baseline(self, domain: str) -> bool:
        return domain in self._baseline

    def set_baseline(self, domain: str, vectors: list[list[float]]) -> None:
        """Called once, the first time a domain's unsupervised models are
        fit — later refits update the live buffer but must not move the
        baseline, or drift against "yesterday" would be invisible."""
        if domain not in self._baseline:
            self._baseline[domain] = np.asarray(vectors, dtype=np.float64)

    def check(self, domain: str, live_vectors: list[list[float]]) -> tuple[dict[str, float], dict[str, float], bool]:
        baseline = self._baseline.get(domain)
        if baseline is None or len(live_vectors) < 10:
            names = feature_names(domain)
            zeros = {n: 0.0 for n in names}
            return zeros, zeros, False

        live = np.asarray(live_vectors, dtype=np.float64)
        names = feature_names(domain)
        psi_by_feature: dict[str, float] = {}
        ks_by_feature: dict[str, float] = {}
        drift_detected = False

        for i, name in enumerate(names):
            psi = _psi(baseline[:, i], live[:, i])
            ks_result = stats.ks_2samp(baseline[:, i], live[:, i])
            psi_by_feature[name] = psi
            ks_by_feature[name] = float(ks_result.statistic)
            if psi > PSI_DRIFT_THRESHOLD or ks_result.pvalue < KS_PVALUE_THRESHOLD:
                drift_detected = True

        return psi_by_feature, ks_by_feature, drift_detected
