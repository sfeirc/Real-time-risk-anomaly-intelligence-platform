"""Per-entity stochastic tick generators.

Market: geometric Brownian motion price process with a stochastic-vol nudge
during `volatility_spike`/`regime_change` scenarios. Payments: log-normal
transaction amounts over a per-merchant customer pool, with fraud bursts
concentrated on a small "compromised" subset of accounts — mirrors how real
card-fraud clusters look (few accounts, many rapid high-value transactions)
far more than uniformly-random anomalies would.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from dataclasses import dataclass, field

from .entities import FOREIGN_COUNTRIES, MarketSymbolSpec, MerchantSpec
from .models import MarketPayload, PaymentsPayload
from .scenarios import Scenario

SECONDS_PER_YEAR = 365 * 24 * 3600.0

_CURRENCY_BY_COUNTRY = {
    "US": "USD", "GB": "GBP", "FR": "EUR", "DE": "EUR", "JP": "JPY",
    "BR": "BRL", "NG": "NGN", "SG": "SGD", "AE": "AED", "CA": "CAD",
}


def _currency_for(country: str) -> str:
    return _CURRENCY_BY_COUNTRY.get(country, "USD")


@dataclass
class MarketEntity:
    spec: MarketSymbolSpec
    rng: random.Random
    price: float = field(init=False)
    live_annual_vol: float = field(init=False)
    live_drift: float = field(init=False)
    seq: int = 0

    def __post_init__(self) -> None:
        self.price = self.spec.base_price
        self.live_annual_vol = self.spec.annual_vol
        self.live_drift = 0.0

    def apply_regime_shift(self, params: dict) -> None:
        self.live_annual_vol = max(0.05, self.live_annual_vol * params["vol_multiplier"])
        self.live_drift += params["drift_shift"] * self.live_annual_vol

    def tick(self, dt_s: float, scenario: Scenario | None) -> tuple[MarketPayload, bool]:
        vol = self.live_annual_vol
        if scenario is not None and scenario.scenario_type == "volatility_spike":
            vol = vol * scenario.params["vol_multiplier"]

        dt_years = max(dt_s, 1e-4) / SECONDS_PER_YEAR
        z = self.rng.gauss(0, 1)
        log_return = (self.live_drift - 0.5 * vol * vol) * dt_years + vol * math.sqrt(dt_years) * z
        self.price = max(1e-6, self.price * math.exp(log_return))

        vol_ratio = vol / self.spec.annual_vol
        spread_bps = self.spec.base_spread_bps * (0.7 + 0.3 * vol_ratio)
        spread = self.price * spread_bps / 1e4
        bid = self.price - spread / 2
        ask = self.price + spread / 2

        side = "buy" if self.rng.random() < 0.5 else "sell"
        size = self.rng.lognormvariate(math.log(0.08), 1.1)
        latency = self.rng.lognormvariate(math.log(4.0), 0.5)
        if scenario is not None and scenario.scenario_type == "latency_incident":
            latency *= scenario.params["latency_multiplier"]

        self.seq += 1
        payload = MarketPayload(
            symbol=self.spec.symbol,
            price=round(self.price, 2),
            size=round(size, 6),
            side=side,
            bid=round(bid, 2),
            ask=round(ask, 2),
            exchange_latency_ms=round(latency, 3),
        )

        corrupted = False
        if (
            scenario is not None
            and scenario.scenario_type == "data_corruption"
            and self.rng.random() < scenario.params["corruption_rate"]
        ):
            payload = self._corrupt(payload)
            corrupted = True
        return payload, corrupted

    def _corrupt(self, payload: MarketPayload) -> MarketPayload:
        kind = self.rng.choice(["negative_price", "crossed_book", "extreme_latency", "zero_size"])
        d = payload.model_dump()
        if kind == "negative_price":
            d["price"] = -abs(d["price"])
        elif kind == "crossed_book":
            d["bid"], d["ask"] = d["ask"] + abs(d["ask"]) * 0.01, d["bid"]
        elif kind == "extreme_latency":
            d["exchange_latency_ms"] = self.rng.uniform(5_000, 60_000)
        elif kind == "zero_size":
            d["size"] = 0.0
        return MarketPayload(**d)


@dataclass
class PaymentsEntity:
    spec: MerchantSpec
    rng: random.Random
    live_mean_amount: float = field(init=False)
    live_decline_rate: float = field(init=False)
    customer_pool: list[str] = field(init=False)
    compromised: set[str] = field(default_factory=set)
    seq: int = 0

    def __post_init__(self) -> None:
        self.live_mean_amount = self.spec.mean_amount
        self.live_decline_rate = self.spec.base_decline_rate
        self.customer_pool = [
            hashlib.sha256(f"{self.spec.merchant_id}:{i}".encode()).hexdigest()[:16]
            for i in range(self.spec.customer_pool_size)
        ]

    def apply_regime_shift(self, params: dict) -> None:
        self.live_mean_amount *= params["amount_multiplier"]
        self.live_decline_rate = min(0.9, self.live_decline_rate * params["decline_rate_multiplier"])

    def tick(self, scenario: Scenario | None) -> tuple[PaymentsPayload, bool]:
        mean_amount = self.live_mean_amount
        decline_rate = self.live_decline_rate
        channel_mix = self.spec.channel_mix
        country = self.spec.home_country
        # light Zipf-ish skew: 80% of traffic hits the first 20% of the pool
        if self.rng.random() < 0.8:
            account = self.rng.choice(self.customer_pool[: max(1, len(self.customer_pool) // 5)])
        else:
            account = self.rng.choice(self.customer_pool)
        cross_border = self.rng.random() < 0.05

        if scenario is not None and scenario.scenario_type == "fraud_pattern":
            if not self.compromised:
                n = max(1, int(len(self.customer_pool) * scenario.params["compromised_fraction"]))
                self.compromised = set(self.rng.sample(self.customer_pool, n))
            account = self.rng.choice(list(self.compromised))
            mean_amount = mean_amount * scenario.params["amount_multiplier"]
            decline_rate = scenario.params["decline_rate_override"]
            cross_border = self.rng.random() < scenario.params["cross_border_prob"]
            channel_mix = {"card_not_present": 1.0}
        else:
            self.compromised.clear()

        amount = self.rng.lognormvariate(math.log(max(mean_amount, 0.5)), self.spec.amount_sigma)
        channel = self.rng.choices(list(channel_mix.keys()), weights=list(channel_mix.values()))[0]
        if cross_border:
            pool = [c for c in FOREIGN_COUNTRIES if c != self.spec.home_country]
            country = self.rng.choice(pool)

        latency = self.rng.lognormvariate(math.log(80.0), 0.6)
        if scenario is not None and scenario.scenario_type == "latency_incident":
            latency *= scenario.params["latency_multiplier"]

        roll = self.rng.random()
        status = "declined" if roll < decline_rate else ("error" if roll < decline_rate + 0.002 else "approved")

        self.seq += 1
        payload = PaymentsPayload(
            txn_id=uuid.uuid4(),
            merchant_id=self.spec.merchant_id,
            account_id_hash=account,
            amount=round(amount, 2),
            currency=_currency_for(self.spec.home_country),
            channel=channel,
            country=country,
            processing_latency_ms=round(latency, 3),
            status=status,
        )

        corrupted = False
        if (
            scenario is not None
            and scenario.scenario_type == "data_corruption"
            and self.rng.random() < scenario.params["corruption_rate"]
        ):
            payload = self._corrupt(payload)
            corrupted = True
        return payload, corrupted

    def _corrupt(self, payload: PaymentsPayload) -> PaymentsPayload:
        kind = self.rng.choice(["negative_amount", "bad_currency", "extreme_latency", "empty_hash"])
        d = payload.model_dump(mode="json")
        if kind == "negative_amount":
            d["amount"] = -abs(d["amount"])
        elif kind == "bad_currency":
            d["currency"] = "XXX"
        elif kind == "extreme_latency":
            d["processing_latency_ms"] = self.rng.uniform(10_000, 120_000)
        elif kind == "empty_hash":
            d["account_id_hash"] = ""
        return PaymentsPayload(**d)
