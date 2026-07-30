"""Anomaly scenario injection — the ground-truth generator for tests/eval.

Six scenario types map directly to the project brief ("volatilite anormale,
fraude, incident operationnel, hausse de latence, donnees corrompues") plus
`regime_change`, which exists specifically to exercise drift detection (see
docs/metrics.md): its *effect* on the entity's baseline is permanent, but its
*label* only covers a short transition window. After the label expires the
new baseline is simply "normal" — the interesting question for the eval
harness is whether the statistical detectors re-baseline correctly (they
should, via EWMA) while the drift monitor still flags that the live
distribution has moved away from the model's training baseline (it should,
via PSI/KS) until the model is retrained. That gap is the point.

Nothing here is imported by feature-service or ml-inference — only by
generators.py (to perturb ticks) and by tests/eval (to score against ground
truth via the `scenario_label` field persisted in ClickHouse `raw_events`).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Literal

ScenarioType = Literal[
    "volatility_spike",
    "fraud_pattern",
    "latency_incident",
    "data_corruption",
    "regime_change",
    "volume_spike",
]

MARKET_SCENARIOS: list[ScenarioType] = [
    "volatility_spike", "latency_incident", "data_corruption", "regime_change", "volume_spike",
]
PAYMENTS_SCENARIOS: list[ScenarioType] = [
    "fraud_pattern", "latency_incident", "data_corruption", "regime_change", "volume_spike",
]


@dataclass
class Scenario:
    scenario_type: ScenarioType
    domain: str
    entity_key: str
    start_ts: float
    duration_s: float
    params: dict = field(default_factory=dict)

    def is_active(self, now: float) -> bool:
        return self.start_ts <= now < self.start_ts + self.duration_s


def _sample_params(scenario_type: ScenarioType, rng: random.Random) -> tuple[dict, float]:
    """Returns (params, duration_s) for a freshly spawned scenario."""
    if scenario_type == "volatility_spike":
        return {"vol_multiplier": rng.uniform(5.0, 12.0)}, rng.uniform(20, 60)
    if scenario_type == "fraud_pattern":
        return {
            "amount_multiplier": rng.uniform(3.0, 9.0),
            "cross_border_prob": rng.uniform(0.5, 0.9),
            "decline_rate_override": rng.uniform(0.15, 0.4),
            "compromised_fraction": rng.uniform(0.01, 0.05),
        }, rng.uniform(10, 40)
    if scenario_type == "latency_incident":
        return {"latency_multiplier": rng.uniform(8.0, 30.0)}, rng.uniform(15, 45)
    if scenario_type == "data_corruption":
        return {"corruption_rate": rng.uniform(0.3, 0.8)}, rng.uniform(10, 30)
    if scenario_type == "regime_change":
        return {
            "vol_multiplier": rng.uniform(1.5, 3.0),
            "drift_shift": rng.uniform(-0.4, 0.4),
            "amount_multiplier": rng.uniform(1.3, 2.2),
            "decline_rate_multiplier": rng.uniform(1.5, 3.0),
        }, rng.uniform(45, 90)
    if scenario_type == "volume_spike":
        return {"rate_multiplier": rng.uniform(5.0, 15.0)}, rng.uniform(10, 40)
    raise ValueError(scenario_type)


class ScenarioManager:
    """One active scenario per (domain, entity_key) at a time."""

    def __init__(self, probability_per_tick: float, seed: int | None = None) -> None:
        self._active: dict[tuple[str, str], Scenario] = {}
        self._probability = probability_per_tick
        self._rng = random.Random(seed)
        self.on_regime_change = None  # callback(domain, entity_key, params), set by generators.py

    def get_active(self, domain: str, entity_key: str, now: float) -> Scenario | None:
        key = (domain, entity_key)
        sc = self._active.get(key)
        if sc is None:
            return None
        if not sc.is_active(now):
            del self._active[key]
            return None
        return sc

    def maybe_spawn(self, domain: str, entity_key: str, now: float) -> Scenario | None:
        if self.get_active(domain, entity_key, now) is not None:
            return None
        if self._rng.random() >= self._probability:
            return None
        pool = MARKET_SCENARIOS if domain == "market" else PAYMENTS_SCENARIOS
        scenario_type = self._rng.choice(pool)
        return self.inject(domain, entity_key, scenario_type, now=now)

    def inject(
        self,
        domain: str,
        entity_key: str,
        scenario_type: ScenarioType,
        duration_s: float | None = None,
        now: float | None = None,
    ) -> Scenario:
        now = now if now is not None else time.time()
        params, sampled_duration = _sample_params(scenario_type, self._rng)
        sc = Scenario(
            scenario_type=scenario_type,
            domain=domain,
            entity_key=entity_key,
            start_ts=now,
            duration_s=duration_s if duration_s is not None else sampled_duration,
            params=params,
        )
        self._active[(domain, entity_key)] = sc
        if scenario_type == "regime_change" and self.on_regime_change is not None:
            self.on_regime_change(domain, entity_key, params)
        return sc

    def snapshot(self) -> list[dict]:
        now = time.time()
        return [
            {
                "domain": sc.domain,
                "entity_key": sc.entity_key,
                "scenario_type": sc.scenario_type,
                "remaining_s": round(sc.start_ts + sc.duration_s - now, 1),
                "params": sc.params,
            }
            for sc in self._active.values()
            if sc.is_active(now)
        ]
