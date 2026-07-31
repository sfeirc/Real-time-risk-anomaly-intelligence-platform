"""Orchestrates one domain-agnostic scoring pipeline: feature vector ->
per-detector scores -> ensemble -> rules -> explanation -> AlertEvent.
Also owns the periodic drift check and ModelMetricsEvent emission.

Deliberately holds all per-domain/per-entity detector state in one place
(rather than scattering dicts-of-detectors through main.py) so the
request/response shape of `process()` stays simple: one FeatureEvent in,
zero-or-one AlertEvent out.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from .config import Settings
from .detectors.autoencoder import RollingAutoencoder
from .detectors.isolation_forest import RollingIsolationForest
from .detectors.statistical import CusumDetector, EwmaRunRulesDetector, ZScoreDetector
from .detectors.velocity import RollingVelocity
from .detectors.xgboost_detector import XGBoostDetector
from .drift import DriftMonitor
from .ensemble import combine
from .explain import classify_probable_cause, compute_top_features
from .features import to_vector
from .rules import RulesEngine
from .schemas import (
    AlertEvent,
    DetectorScores,
    Explanation,
    FeatureEvent,
    ModelMetricsEvent,
)

log = logging.getLogger("ml-inference.pipeline")

DOMAINS = ("market", "payments")

# Fixed, arbitrary namespace for deriving alert_id deterministically (any
# uuid4 works here - it just needs to never change once chosen, since
# changing it would change every alert_id this service has ever produced).
_ALERT_ID_NAMESPACE = uuid.UUID("7c6f1a9e-2b3d-4f5c-8a1e-9d6b4c2f0e7a")


def _alert_id(domain: str, entity_key: str, window_end: str) -> uuid.UUID:
    """Deterministic, not random: `enable.auto.commit` (see
    docs/roadmap.md "Kafka semantics") means a crash between scoring a
    window and the next offset commit reprocesses that window on restart.
    A random alert_id would turn that into a second, permanently-stored
    alert for the same window; deriving it from the window's own identity
    instead makes reprocessing produce the *same* alert_id both times, which
    is what makes `risk.alerts` idempotent under ReplacingMergeTree (see
    infra/clickhouse/init/01_schema.sql) - the natural key is
    (domain, entity_key, window_end), not model_version, because the same
    window scored again is still the same real-world event even if the
    model happened to change in between."""
    return uuid.uuid5(_ALERT_ID_NAMESPACE, f"{domain}|{entity_key}|{window_end}")


class _DomainMetricsAccumulator:
    def __init__(self) -> None:
        self.events_scored = 0
        self.inference_times_ms: deque[float] = deque(maxlen=2000)
        self.window_start = time.monotonic()

    def record(self, inference_ms: float) -> None:
        self.events_scored += 1
        self.inference_times_ms.append(inference_ms)

    def reset(self) -> None:
        self.events_scored = 0
        self.inference_times_ms.clear()
        self.window_start = time.monotonic()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(len(s) * p), len(s) - 1)
    return s[idx]


class MLPipeline:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.zscore_detector = ZScoreDetector()
        self.ewma_rules = EwmaRunRulesDetector()
        self.cusum = CusumDetector(k=cfg.cusum_slack_k, h=cfg.cusum_threshold_h)
        self.velocity = RollingVelocity(window_count=cfg.velocity_window_count)

        self.iso_forests = {
            d: RollingIsolationForest(
                buffer_size=cfg.buffer_size,
                min_buffer=cfg.min_buffer_for_training,
                retrain_every=cfg.retrain_every_n_windows,
                n_estimators=cfg.isolation_forest_n_estimators,
                contamination=cfg.isolation_forest_contamination,
            )
            for d in DOMAINS
        }
        self.autoencoders = {
            d: RollingAutoencoder(
                buffer_size=cfg.buffer_size,
                min_buffer=cfg.min_buffer_for_training,
                retrain_every=cfg.retrain_every_n_windows,
                hidden_dim=cfg.autoencoder_hidden_dim,
                latent_dim=cfg.autoencoder_latent_dim,
                epochs=cfg.autoencoder_epochs,
                lr=cfg.autoencoder_lr,
            )
            for d in DOMAINS
        }
        self.xgboost_detectors = {d: XGBoostDetector(cfg.xgboost_model_path.format(domain=d), d) for d in DOMAINS}
        self.drift = DriftMonitor()
        self.rules = RulesEngine(cfg.rules_path)

        self._warmup_logged = {d: False for d in DOMAINS}
        self._metrics_acc = {d: _DomainMetricsAccumulator() for d in DOMAINS}
        self._last_drift_flag: dict[str, bool] = {d: False for d in DOMAINS}

    def process(self, f: FeatureEvent) -> AlertEvent | None:
        started = time.perf_counter()
        velocity_count = self.velocity.observe_and_get(f.entity_key, f.count)
        vector = to_vector(f, velocity_count)
        domain = f.domain

        iso = self.iso_forests[domain]
        ae = self.autoencoders[domain]
        xgb_det = self.xgboost_detectors[domain]

        iso.observe(vector)
        ae.observe(vector)
        if iso.ready and ae.ready and not self._warmup_logged[domain]:
            self._warmup_logged[domain] = True
            log.info("warmup_complete", extra={"domain": domain})

        if iso.ready and not self.drift.has_baseline(domain):
            self.drift.set_baseline(domain, iso.buffer_snapshot())

        detectors = DetectorScores(
            zscore=self.zscore_detector.score(f.zscore),
            ewma=self.ewma_rules.score(f.entity_key, f.zscore),
            cusum=self.cusum.score(f.entity_key, f.zscore),
            isolation_forest=iso.score(vector),
            autoencoder=ae.score(vector),
            xgboost=xgb_det.score(vector),
        )
        anomaly_score = combine(detectors)

        self._metrics_acc[domain].record((time.perf_counter() - started) * 1000.0)

        severity, action = self.rules.evaluate(domain, anomaly_score)
        if severity is None or action is None:
            return None

        top_feats = compute_top_features(domain, vector, iso.mean, iso.std)
        probable_cause = classify_probable_cause(domain, f, top_feats, detectors)

        now = datetime.now(timezone.utc)
        window_start_dt = datetime.fromisoformat(f.window_start)
        latency_ms = (now - window_start_dt).total_seconds() * 1000.0

        return AlertEvent(
            alert_id=_alert_id(domain, f.entity_key, f.window_end),
            entity_key=f.entity_key,
            domain=domain,
            ts=now.isoformat(),
            window_end=f.window_end,
            anomaly_score=anomaly_score,
            severity=severity,
            action=action,
            detectors=detectors,
            explanation=Explanation(probable_cause=probable_cause, top_features=top_feats),
            model_version=self.cfg.model_version,
            drift_flag=self._last_drift_flag.get(domain, False),
            latency_ingest_to_alert_ms=max(latency_ms, 0.0),
        )

    # --- periodic drift / model-metrics emission ---------------------------

    def check_drift_and_build_metrics(self, domain: str) -> ModelMetricsEvent:
        iso = self.iso_forests[domain]
        live_vectors = iso.buffer_snapshot()
        psi_by_feature, ks_by_feature, drift_detected = self.drift.check(domain, live_vectors)
        self._last_drift_flag[domain] = drift_detected

        acc = self._metrics_acc[domain]
        elapsed = max(time.monotonic() - acc.window_start, 1e-6)
        times = list(acc.inference_times_ms)
        event = ModelMetricsEvent(
            model_id=f"ensemble-{domain}",
            model_version=self.cfg.model_version,
            ts=datetime.now(timezone.utc).isoformat(),
            eval_window_s=elapsed,
            precision=None,
            recall=None,
            f1=None,
            false_positive_rate=None,
            psi_by_feature=psi_by_feature,
            ks_stat_by_feature=ks_by_feature,
            drift_detected=drift_detected,
            events_scored=acc.events_scored,
            throughput_eps=acc.events_scored / elapsed,
            p50_inference_ms=_percentile(times, 0.50),
            p99_inference_ms=_percentile(times, 0.99),
        )
        acc.reset()
        return event
