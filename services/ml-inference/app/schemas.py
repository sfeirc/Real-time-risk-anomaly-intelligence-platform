"""Mirrors docs/data-contracts.md sections 2-4 / schemas/*.schema.json."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Domain = Literal["market", "payments"]


class FeatureEvent(BaseModel):
    entity_key: str
    domain: Domain
    window_start: str
    window_end: str
    window_size_s: float
    count: int
    throughput_eps: float

    latency_p50_ms: float
    latency_p99_ms: float
    error_rate: float

    vwap: float | None = None
    spread_bps: float | None = None
    realized_vol: float | None = None
    order_imbalance: float | None = None

    mean_amount: float | None = None
    sum_amount: float | None = None
    decline_rate: float | None = None
    distinct_accounts: int | None = None

    ewma_mean: float
    ewma_var: float
    zscore: float
    primary_metric: float


ProbableCause = Literal[
    "volatility_spike", "latency_incident", "fraud_pattern",
    "data_corruption", "regime_change", "volume_spike", "unknown",
]
Severity = Literal["watch", "alert", "critical"]
Action = Literal["watch", "alert", "block"]


class DetectorScores(BaseModel):
    zscore: float
    ewma: float
    cusum: float
    isolation_forest: float
    autoencoder: float
    xgboost: float | None = None


class TopFeature(BaseModel):
    feature: str
    value: float
    baseline: float
    contribution: float


class Explanation(BaseModel):
    probable_cause: ProbableCause
    top_features: list[TopFeature]


class AlertEvent(BaseModel):
    alert_id: UUID
    entity_key: str
    domain: Domain
    ts: str
    window_end: str
    anomaly_score: float
    severity: Severity
    action: Action
    detectors: DetectorScores
    explanation: Explanation
    model_version: str
    drift_flag: bool
    latency_ingest_to_alert_ms: float


class ModelMetricsEvent(BaseModel):
    model_id: str
    model_version: str
    ts: str
    eval_window_s: float
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    false_positive_rate: float | None = None
    psi_by_feature: dict[str, float]
    ks_stat_by_feature: dict[str, float]
    drift_detected: bool
    events_scored: int
    throughput_eps: float
    p50_inference_ms: float
    p99_inference_ms: float
