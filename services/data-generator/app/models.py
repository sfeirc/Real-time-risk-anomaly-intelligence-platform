"""Pydantic mirror of schemas/raw_event.schema.json — see docs/data-contracts.md.

`scenario_label`/`corrupted` are ground truth for tests/eval only. Nothing
downstream of ingestion (feature-service, ml-inference) is allowed to read
`scenario_label` as a detection input — see docs/metrics.md.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Domain = Literal["market", "payments"]
Side = Literal["buy", "sell"]
Channel = Literal["card_present", "card_not_present", "wire", "ach"]
Status = Literal["approved", "declined", "error"]


class MarketPayload(BaseModel):
    symbol: str
    price: float
    size: float
    side: Side
    bid: float
    ask: float
    exchange_latency_ms: float


class PaymentsPayload(BaseModel):
    txn_id: UUID
    merchant_id: str
    account_id_hash: str
    amount: float
    currency: str
    channel: Channel
    country: str
    processing_latency_ms: float
    status: Status


class RawEvent(BaseModel):
    event_id: UUID
    domain: Domain
    entity_key: str
    source: str
    seq: int
    ts_event: str  # RFC3339, set at generation time
    ts_ingest: str | None = None  # stamped by the ingestion service, not here
    corrupted: bool = False
    scenario_label: str | None = None
    payload: MarketPayload | PaymentsPayload
