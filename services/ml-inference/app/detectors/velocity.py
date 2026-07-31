"""Multi-window "velocity" feature: a rolling sum of each window's event
`count` over the last `window_count` windows, per entity. ml-inference only
ever sees pre-aggregated feature windows (2s market / 5s payments, see
docs/data-contracts.md), never individual raw events, so this is the
closest analogue available here to the multi-timeframe transaction-count
features real fraud-detection feature stores build (e.g. "count in the last
1hr/24hr/7day") - a signal that spans several windows, not just the current
one, catching a sustained volume ramp that looks unremarkable in any single
short window. `throughput_eps`/`count` already cover "how busy is *this*
window"; this covers "how busy has this entity been *recently*".
"""

from __future__ import annotations

from collections import defaultdict, deque


class RollingVelocity:
    def __init__(self, window_count: int) -> None:
        self._window_count = window_count
        self._history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=window_count))

    def observe_and_get(self, entity_key: str, count: float) -> float:
        history = self._history[entity_key]
        history.append(count)
        return float(sum(history))
