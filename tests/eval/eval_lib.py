"""Core evaluation logic, kept dependency-free of ClickHouse/HTTP so it's
directly unit-testable (see test_eval_lib.py) — run_eval.py is a thin I/O
shell around this module.

Ground truth: `scenario_label` on `risk.raw_events`, set only by
data-generator's scenario injector and never read by ml-inference (see
docs/metrics.md — this is the one comparison in the whole project allowed
to touch it). A "positive" is a features window with at least one labeled
raw event inside [window_start, window_end); a "detection" is that same
window producing a row in risk.alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Window:
    entity_key: str
    domain: str
    window_start: datetime
    window_end: datetime
    scenario_label: str | None  # ground truth; None/"" = normal
    alerted: bool


@dataclass
class Episode:
    entity_key: str
    domain: str
    scenario_label: str
    start: datetime
    end: datetime
    detected_at: datetime | None  # window_end of the first alerting window in-episode


@dataclass
class ConfusionCounts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    @property
    def false_positive_rate(self) -> float | None:
        return self.fp / (self.fp + self.tn) if (self.fp + self.tn) > 0 else None


def confusion_matrix(windows: list[Window]) -> ConfusionCounts:
    c = ConfusionCounts()
    for w in windows:
        is_anomalous = bool(w.scenario_label)
        if is_anomalous and w.alerted:
            c.tp += 1
        elif is_anomalous and not w.alerted:
            c.fn += 1
        elif not is_anomalous and w.alerted:
            c.fp += 1
        else:
            c.tn += 1
    return c


def confusion_by_scenario(windows: list[Window]) -> dict[str, ConfusionCounts]:
    """Recall per scenario type: precision/FPR aren't meaningful split this
    way (a false positive has no ground-truth scenario to attribute it to),
    so callers should read `.recall` off each entry and use the overall
    confusion_matrix() for precision/FPR."""
    by_scenario: dict[str, list[Window]] = {}
    for w in windows:
        if w.scenario_label:
            by_scenario.setdefault(w.scenario_label, []).append(w)
    return {label: confusion_matrix(ws) for label, ws in by_scenario.items()}


def extract_episodes(windows: list[Window]) -> list[Episode]:
    """Groups contiguous same-label windows per entity into episodes (a
    scenario spans many windows; that's one event, not N independent
    ones) and records when — if ever — the episode was first detected.
    Assumes `windows` is already sorted by (entity_key, window_start).
    """
    episodes: list[Episode] = []
    current: Episode | None = None

    def close(ep: Episode | None) -> None:
        if ep is not None:
            episodes.append(ep)

    for w in windows:
        label = w.scenario_label or None
        same_episode = (
            current is not None
            and current.entity_key == w.entity_key
            and current.scenario_label == label
            and w.window_start <= current.end  # contiguous / overlapping
        )
        if label is None:
            close(current)
            current = None
            continue
        if same_episode:
            current.end = w.window_end  # type: ignore[union-attr]
            if current.detected_at is None and w.alerted:  # type: ignore[union-attr]
                current.detected_at = w.window_end  # type: ignore[union-attr]
        else:
            close(current)
            current = Episode(
                entity_key=w.entity_key,
                domain=w.domain,
                scenario_label=label,
                start=w.window_start,
                end=w.window_end,
                detected_at=w.window_end if w.alerted else None,
            )
    close(current)
    return episodes


def mean_detection_delay_s(episodes: list[Episode]) -> dict[str, float | None]:
    """Mean seconds from episode start to first detection, per scenario
    type. `None` for a scenario type with zero detected episodes (nothing
    to average) — distinct from 0, which would mean "detected instantly"."""
    by_label: dict[str, list[float]] = {}
    for ep in episodes:
        if ep.detected_at is not None:
            by_label.setdefault(ep.scenario_label, []).append((ep.detected_at - ep.start).total_seconds())
    return {label: (sum(delays) / len(delays)) for label, delays in by_label.items()}
