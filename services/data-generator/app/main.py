"""Synthetic market + payments tick source.

Streams RawEvent JSON (docs/data-contracts.md) over a WebSocket at
`/stream`, continuously, regardless of whether a consumer is connected yet —
`ingestion` can restart and resume without the generator's internal state
(prices, active scenarios) resetting. Anomaly scenarios are injected either
automatically (`DATA_GENERATOR_SCENARIO_PROBABILITY` per entity per second)
or on demand via `POST /inject` (used by demos and by `tests/eval`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .config import settings
from .entities import MARKET_SYMBOLS, MERCHANTS
from .generators import MarketEntity, PaymentsEntity
from .models import RawEvent
from .scenarios import ScenarioManager, ScenarioType

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("data-generator")

MARKET_SOURCE = "synthetic-exchange-1"
PAYMENTS_SOURCE = "synthetic-psp-1"


class _Entity:
    __slots__ = ("base_weight", "domain", "entity_key", "impl", "last_ts")

    def __init__(self, domain: str, entity_key: str, impl, base_weight: float) -> None:
        self.domain = domain
        self.entity_key = entity_key
        self.impl = impl
        self.base_weight = base_weight
        self.last_ts = time.monotonic()


class GeneratorState:
    def __init__(self) -> None:
        rng = random.Random(settings.seed)
        self.scenarios = ScenarioManager(settings.scenario_probability, seed=settings.seed)
        self.scenarios.on_regime_change = self._on_regime_change

        self.entities: list[_Entity] = []
        for spec in MARKET_SYMBOLS:
            impl = MarketEntity(spec, random.Random(rng.random()))
            self.entities.append(_Entity("market", spec.symbol, impl, spec.weight * settings.market_share))
        for spec in MERCHANTS:
            impl = PaymentsEntity(spec, random.Random(rng.random()))
            self.entities.append(_Entity("payments", spec.merchant_id, impl, spec.weight * (1 - settings.market_share)))

        self._by_key = {(e.domain, e.entity_key): e for e in self.entities}
        self.clients: set[WebSocket] = set()
        self.seq_global = 0
        self._last_scenario_check = 0.0

    def _on_regime_change(self, domain: str, entity_key: str, params: dict) -> None:
        e = self._by_key.get((domain, entity_key))
        if e is not None:
            e.impl.apply_regime_shift(params)

    def _weight(self, e: _Entity, now: float) -> float:
        sc = self.scenarios.get_active(e.domain, e.entity_key, now)
        if sc is not None and sc.scenario_type == "volume_spike":
            return e.base_weight * sc.params["rate_multiplier"]
        return e.base_weight

    def maybe_spawn_scenarios(self, now: float) -> None:
        if now - self._last_scenario_check < 1.0:
            return
        self._last_scenario_check = now
        for e in self.entities:
            self.scenarios.maybe_spawn(e.domain, e.entity_key, now)

    def next_event(self) -> RawEvent:
        now_wall = time.time()
        now_mono = time.monotonic()
        self.maybe_spawn_scenarios(now_wall)

        weights = [self._weight(e, now_wall) for e in self.entities]
        entity = random.choices(self.entities, weights=weights, k=1)[0]
        scenario = self.scenarios.get_active(entity.domain, entity.entity_key, now_wall)
        dt_s = max(now_mono - entity.last_ts, 1e-3)
        entity.last_ts = now_mono

        if entity.domain == "market":
            payload, corrupted = entity.impl.tick(dt_s, scenario)
            source = MARKET_SOURCE
        else:
            payload, corrupted = entity.impl.tick(scenario)
            source = PAYMENTS_SOURCE

        self.seq_global += 1
        return RawEvent(
            event_id=uuid.uuid4(),
            domain=entity.domain,
            entity_key=entity.entity_key,
            source=source,
            seq=entity.impl.seq,
            ts_event=datetime.now(timezone.utc).isoformat(),
            corrupted=corrupted,
            scenario_label=scenario.scenario_type if scenario is not None else None,
            payload=payload,
        )


state = GeneratorState()


async def _producer_loop() -> None:
    interval = 1.0 / max(settings.events_per_sec, 1.0)
    while True:
        started = time.monotonic()
        event = state.next_event()
        if state.clients:
            data = event.model_dump_json()
            dead = []
            for ws in state.clients:
                try:
                    await ws.send_text(data)
                except Exception:  # noqa: BLE001 — one bad client must not break the broadcast fan-out
                    dead.append(ws)
            for ws in dead:
                state.clients.discard(ws)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, interval - elapsed))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_producer_loop())
    log.info("data-generator started: %d entities, target %.1f events/s", len(state.entities), settings.events_per_sec)
    yield
    task.cancel()


app = FastAPI(title="real-time-risk data-generator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "clients": len(state.clients), "entities": len(state.entities)}


@app.get("/entities")
async def entities() -> dict:
    return {
        "market": [e.entity_key for e in state.entities if e.domain == "market"],
        "payments": [e.entity_key for e in state.entities if e.domain == "payments"],
    }


@app.get("/scenarios")
async def scenarios() -> list[dict]:
    return state.scenarios.snapshot()


class InjectRequest(BaseModel):
    domain: str
    entity_key: str
    scenario: ScenarioType
    duration_s: float | None = None


@app.post("/inject")
async def inject(req: InjectRequest) -> dict:
    sc = state.scenarios.inject(req.domain, req.entity_key, req.scenario, duration_s=req.duration_s)
    return {"status": "injected", "scenario_type": sc.scenario_type, "duration_s": sc.duration_s, "params": sc.params}


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    state.clients.add(ws)
    log.info("client connected, total=%d", len(state.clients))
    try:
        while True:
            # keep the connection open; client isn't expected to send anything,
            # but reading lets us detect disconnects promptly.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.clients.discard(ws)
        log.info("client disconnected, total=%d", len(state.clients))
